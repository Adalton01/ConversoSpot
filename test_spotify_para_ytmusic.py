import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

import spotify_para_ytmusic as app


def api_result(video_id: str, title: str, artist: str) -> dict:
    return {
        "videoId": video_id,
        "title": title,
        "artists": [{"name": artist}],
    }


def http_response(status: int, body: str, content_type: str) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = body.encode("utf-8")
    response.headers["Content-Type"] = content_type
    response.url = "https://music.youtube.com/youtubei/v1/search"
    response.reason = "Forbidden" if status == 403 else "Error"
    return response


class FakeSearchClient:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.filters: list[str] = []

    def search(self, query: str, filter: str, limit: int) -> list[dict]:
        self.filters.append(filter)
        outcome = self.responses[filter]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakePlaylistClient(FakeSearchClient):
    def __init__(self, responses: dict[str, object], tracks: list[dict] | None = None):
        super().__init__(responses)
        self.tracks = tracks or []
        self.added_batches: list[list[str]] = []
        self.created = 0

    def get_playlist(self, playlistId: str, limit: int | None = None) -> dict:
        return {"tracks": self.tracks}

    def create_playlist(self, **kwargs) -> str:
        self.created += 1
        return "new-playlist"

    def add_playlist_items(self, playlistId: str, videoIds: list[str]) -> str:
        self.added_batches.append(videoIds)
        return "STATUS_SUCCEEDED"


class SearchTests(unittest.TestCase):
    def test_found_track(self) -> None:
        client = FakeSearchClient({
            "songs": [api_result("id-1", "Song", "Artist")],
            "videos": [],
        })
        self.assertEqual(app.buscar_no_ytmusic(client, "Song", "Artist"), ("id-1", 1.0))
        self.assertEqual(client.filters, ["songs"])

    def test_not_found_after_both_filters_succeed(self) -> None:
        client = FakeSearchClient({"songs": [], "videos": []})
        self.assertEqual(app.buscar_no_ytmusic(client, "Missing", "Artist"), (None, 0.0))
        self.assertEqual(client.filters, ["songs", "videos"])

    def test_songs_error_falls_back_to_videos(self) -> None:
        client = FakeSearchClient({
            "songs": RuntimeError("songs unavailable"),
            "videos": [api_result("video-1", "Song", "Artist")],
        })
        self.assertEqual(app.buscar_no_ytmusic(client, "Song", "Artist"), ("video-1", 1.0))
        self.assertEqual(client.filters, ["songs", "videos"])

    def test_403_stops_before_video_filter(self) -> None:
        blocked = app.YouTubeMusicResponseError(
            "HTTP 403 blocked", status_code=403, category="blocked"
        )
        client = FakeSearchClient({"songs": blocked, "videos": []})
        with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            app.buscar_no_ytmusic(client, "Song", "Artist")
        self.assertEqual(client.filters, ["songs"])

    def test_error_on_one_filter_is_not_reported_as_not_found(self) -> None:
        client = FakeSearchClient({
            "songs": RuntimeError("temporary failure"),
            "videos": [],
        })
        with self.assertRaisesRegex(RuntimeError, "temporary failure"):
            app.buscar_no_ytmusic(client, "Song", "Artist")


class HttpResponseTests(unittest.TestCase):
    def request(self, response: requests.Response) -> tuple[object, object]:
        session = app.YouTubeMusicSession()
        with patch.object(requests.Session, "post", return_value=response) as post:
            with self.assertRaises(app.YouTubeMusicResponseError) as caught:
                session.post(
                    "https://music.youtube.com/youtubei/v1/search",
                    json={},
                )
        return caught.exception, post

    def test_403_html_is_not_parsed_or_retried(self) -> None:
        response = http_response(403, "<html>private challenge</html>", "text/html")
        with patch.object(response, "json", side_effect=AssertionError("parsed HTML")) as parse:
            error, post = self.request(response)
        self.assertEqual(error.status_code, 403)
        self.assertEqual(error.category, "blocked")
        self.assertIn("bloqueou", str(error))
        self.assertNotIn("private challenge", str(error))
        self.assertEqual(post.call_count, 1)
        parse.assert_not_called()

    def test_429_is_distinguished(self) -> None:
        error, post = self.request(http_response(429, "<html>rate limited</html>", "text/html"))
        self.assertEqual(error.status_code, 429)
        self.assertEqual(error.category, "rate_limit")
        self.assertEqual(post.call_count, 1)

    def test_5xx_is_distinguished_without_json_decode(self) -> None:
        for status in (500, 503):
            with self.subTest(status=status):
                error, post = self.request(
                    http_response(status, "<html>upstream failure</html>", "text/html")
                )
                self.assertEqual(error.status_code, status)
                self.assertEqual(error.category, "server")
                self.assertEqual(post.call_count, 1)

    def test_connection_failure_is_distinguished_and_retried_once(self) -> None:
        session = app.YouTubeMusicSession()
        with patch.object(
            requests.Session,
            "post",
            side_effect=[
                requests.ConnectionError("offline"),
                requests.ConnectionError("offline"),
            ],
        ) as post, patch.object(app.time, "sleep"):
            with self.assertRaises(app.YouTubeMusicResponseError) as caught:
                session.post(
                    "https://music.youtube.com/youtubei/v1/search",
                    json={},
                )
        self.assertEqual(caught.exception.category, "connection")
        self.assertIsNone(caught.exception.status_code)
        self.assertEqual(post.call_count, 2)


class ResumeTests(unittest.TestCase):
    def test_resume_skips_processed_search_and_existing_playlist_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "playlist.csv"
            csv_path.write_text(
                "Track Name,Artist Name(s)\nFirst,Artist\nSecond,Artist\n",
                encoding="utf-8",
            )
            tracks = app.ler_csv(str(csv_path))
            state = {
                "playlist_id": "existing-playlist",
                "processadas": [0],
                "video_ids": ["existing-video"],
                "nao_encontradas": [],
                "resultados": {
                    "0": {
                        "titulo": "First",
                        "artista": "Artist",
                        "status": "Encontrada",
                        "video_id": "existing-video",
                    }
                },
                "adicionados": [],
            }
            app.salvar_progresso(str(csv_path), state)
            client = FakePlaylistClient(
                {"songs": [api_result("new-video", "Second", "Artist")], "videos": []},
                tracks=[{"videoId": "existing-video"}],
            )
            with patch.object(app.time, "sleep"):
                result = app.transferir_playlist(
                    client,
                    tracks,
                    "Playlist",
                    str(csv_path),
                    retomar=True,
                )

            self.assertEqual(client.filters, ["songs"])
            self.assertEqual(client.created, 0)
            self.assertEqual(client.added_batches, [["new-video"]])
            self.assertEqual(result["adicionadas"], 2)
            self.assertFalse(app.caminho_progresso(str(csv_path)).exists())

    def test_existing_checkpoint_is_not_overwritten_without_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "playlist.csv"
            csv_path.write_text(
                "Track Name,Artist Name(s)\nFirst,Artist\n",
                encoding="utf-8",
            )
            previous = {
                "playlist_id": None,
                "processadas": [0],
                "video_ids": ["preserved-video"],
                "nao_encontradas": [],
                "resultados": {},
                "adicionados": [],
            }
            app.salvar_progresso(str(csv_path), previous)
            client = FakePlaylistClient({"songs": [], "videos": []})
            with self.assertRaisesRegex(RuntimeError, "--retomar"):
                app.transferir_playlist(
                    client,
                    app.ler_csv(str(csv_path)),
                    "Playlist",
                    str(csv_path),
                )
            self.assertEqual(
                app.carregar_progresso(str(csv_path))["video_ids"],
                ["preserved-video"],
            )

    def test_403_keeps_prior_checkpoint_and_current_track_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "playlist.csv"
            csv_path.write_text(
                "Track Name,Artist Name(s)\nFirst,Artist\nSecond,Artist\n",
                encoding="utf-8",
            )
            tracks = app.ler_csv(str(csv_path))
            blocked = app.YouTubeMusicResponseError(
                "HTTP 403 blocked", status_code=403, category="blocked"
            )

            class InterruptedClient(FakeSearchClient):
                def search(self, query: str, filter: str, limit: int) -> list[dict]:
                    self.filters.append(filter)
                    if query.startswith("Second"):
                        raise blocked
                    return [api_result("first-video", "First", "Artist")]

            client = InterruptedClient({"songs": [], "videos": []})
            with patch.object(app.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                    app.transferir_playlist(
                        client,
                        tracks,
                        "Playlist",
                        str(csv_path),
                    )

            saved = app.carregar_progresso(str(csv_path))
            self.assertEqual(saved["processadas"], [0])
            self.assertEqual(saved["video_ids"], ["first-video"])
            self.assertNotIn("1", saved["resultados"])
            self.assertIsNone(saved["playlist_id"])


if __name__ == "__main__":
    unittest.main()
