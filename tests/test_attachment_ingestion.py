from __future__ import annotations

import base64
import tempfile
import time
import unittest
import urllib.request
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from texting_app import config, settings
from texting_app.attachment_ingestion import (
    AttachmentPermanentError,
    DownloadResult,
    _ProviderRedirectHandler,
    _provider_headers,
    _validate_remote_url,
    backfill_attachment_jobs,
    enqueue_remote_attachment,
    process_attachment_jobs,
)
from texting_app.db import add_attachment, connect, ensure_conversation, init_db, upsert_message
from texting_app.telnyx import _store_webhook_message
from texting_app.telnyx import verify_signature as verify_telnyx_signature
from texting_app.timeutil import now_est
from texting_app.twilio import _store_inbound_message
from texting_app.twilio import verify_signature as verify_twilio_signature


class AttachmentIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(config, "DATA_DIR", root))
        self.stack.enter_context(patch.object(config, "MEDIA_DIR", root / "media"))
        self.stack.enter_context(patch.object(config, "DB_PATH", root / "switchboard.sqlite"))
        self.stack.enter_context(patch.object(config, "PERSONAL_NUMBERS", []))
        self.stack.enter_context(patch.object(config, "TWILIO_ACCOUNT_SID", "AC-test"))
        self.stack.enter_context(patch.object(config, "TWILIO_AUTH_TOKEN", "secret"))
        settings.invalidate_settings_cache()

    def tearDown(self) -> None:
        settings.invalidate_settings_cache()
        self.stack.close()
        self.temp_dir.cleanup()

    def _create_message(self) -> tuple[int, int]:
        conn = connect()
        init_db(conn)
        conversation_id = ensure_conversation(conn, ["+15551234567"], ["+15557654321"])
        message_id = upsert_message(
            conn,
            conversation_id=conversation_id,
            direction="inbound",
            from_number="+15551234567",
            to_numbers=["+15557654321"],
            cc_numbers=[],
            text="photo",
            occurred_at=now_est(),
            source="twilio",
        )
        conn.commit()
        conn.close()
        return conversation_id, message_id

    def test_enqueue_is_atomic_and_idempotent(self) -> None:
        _, message_id = self._create_message()
        conn = connect()
        init_db(conn)

        first_id = enqueue_remote_attachment(
            conn,
            message_id,
            provider="twilio",
            remote_url="https://api.twilio.com/media/ME123",
            content_type="image/jpeg",
            dedupe_key="twilio:SM123:0",
        )
        second_id = enqueue_remote_attachment(
            conn,
            message_id,
            provider="twilio",
            remote_url="https://api.twilio.com/media/ME123",
            content_type="image/jpeg",
            dedupe_key="twilio:SM123:0",
        )
        self.assertEqual(first_id, second_id)

        # Another connection cannot observe the uncommitted metadata or job.
        observer = connect()
        self.assertEqual(observer.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 0)
        self.assertEqual(observer.execute("SELECT COUNT(*) FROM attachment_ingestion_jobs").fetchone()[0], 0)
        observer.close()

        conn.commit()
        attachment = conn.execute("SELECT * FROM attachments WHERE id = ?", (first_id,)).fetchone()
        job = conn.execute("SELECT * FROM attachment_ingestion_jobs WHERE attachment_id = ?", (first_id,)).fetchone()
        self.assertEqual(attachment["ingestion_status"], "pending")
        self.assertIsNone(attachment["local_path"])
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["dedupe_key"], "twilio:SM123:0")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM attachment_ingestion_jobs").fetchone()[0], 1)
        conn.close()

    def test_distinct_provider_media_positions_can_share_a_url(self) -> None:
        _, message_id = self._create_message()
        conn = connect()
        init_db(conn)
        first_id = enqueue_remote_attachment(
            conn,
            message_id,
            provider="twilio",
            remote_url="https://api.twilio.com/media/shared",
            dedupe_key="twilio:SM-shared:0",
        )
        second_id = enqueue_remote_attachment(
            conn,
            message_id,
            provider="twilio",
            remote_url="https://api.twilio.com/media/shared",
            dedupe_key="twilio:SM-shared:1",
        )
        conn.commit()
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM attachment_ingestion_jobs").fetchone()[0], 2)
        conn.close()

    def test_success_updates_attachment_job_and_refresh_parents(self) -> None:
        conversation_id, message_id = self._create_message()
        conn = connect()
        init_db(conn)
        before_message = conn.execute("SELECT updated_at FROM messages WHERE id = ?", (message_id,)).fetchone()[0]
        before_conversation = conn.execute(
            "SELECT updated_at FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()[0]
        attachment_id = enqueue_remote_attachment(
            conn,
            message_id,
            provider="twilio",
            remote_url="https://api.twilio.com/media/ME-ready",
            content_type="image/jpeg",
            dedupe_key="twilio:SM-ready:0",
        )
        conn.commit()
        conn.close()

        seen_jobs = []

        def fake_download(job):
            seen_jobs.append(dict(job))
            return DownloadResult("media/attachment-ready.jpg", 4, "a" * 64, "image/jpeg")

        with patch("texting_app.attachment_ingestion.init_db") as initialize_schema:
            processed = process_attachment_jobs(limit=1, now=time.time() + 1, downloader=fake_download)
        initialize_schema.assert_not_called()
        self.assertEqual(processed, 1)
        self.assertEqual(seen_jobs[0]["provider"], "twilio")

        conn = connect()
        attachment = conn.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
        job = conn.execute("SELECT * FROM attachment_ingestion_jobs WHERE attachment_id = ?", (attachment_id,)).fetchone()
        after_message = conn.execute("SELECT updated_at FROM messages WHERE id = ?", (message_id,)).fetchone()[0]
        after_conversation = conn.execute(
            "SELECT updated_at FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()[0]
        self.assertEqual(attachment["local_path"], "media/attachment-ready.jpg")
        self.assertEqual(attachment["ingestion_status"], "ready")
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["attempts"], 1)
        self.assertNotEqual(after_message, before_message)
        self.assertNotEqual(after_conversation, before_conversation)
        conn.close()

    def test_worker_transcodes_3gp_before_completion(self) -> None:
        _, message_id = self._create_message()
        conn = connect()
        init_db(conn)
        attachment_id = enqueue_remote_attachment(
            conn,
            message_id,
            provider="telnyx",
            remote_url="https://api.telnyx.com/media/clip.3gp",
            content_type="video/3gpp",
            filename="clip.3gp",
            dedupe_key="telnyx:video:0",
        )
        conn.commit()
        conn.close()
        downloaded = DownloadResult("media/attachment-video.3gp", 12, "a" * 64, "video/3gpp")
        converted = DownloadResult(
            "media/attachment-video.mp4",
            20,
            "b" * 64,
            "video/mp4",
            "attachment-video.mp4",
            "a" * 64,
        )
        with patch("texting_app.attachment_ingestion._transcode_video", return_value=converted) as transcode:
            processed = process_attachment_jobs(
                limit=1,
                now=time.time() + 1,
                downloader=lambda _job: downloaded,
            )
        self.assertEqual(processed, 1)
        transcode.assert_called_once()
        conn = connect()
        attachment = conn.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
        self.assertEqual(attachment["local_path"], converted.local_path)
        self.assertEqual(attachment["content_type"], "video/mp4")
        self.assertEqual(attachment["filename"], "attachment-video.mp4")
        self.assertEqual(attachment["sha256"], "a" * 64)
        self.assertEqual(attachment["ingestion_status"], "ready")
        conn.close()

    def test_failure_retries_with_backoff_then_stops(self) -> None:
        conversation_id, message_id = self._create_message()
        conn = connect()
        init_db(conn)
        original_message_updated_at = conn.execute(
            "SELECT updated_at FROM messages WHERE id = ?", (message_id,)
        ).fetchone()[0]
        original_conversation_updated_at = conn.execute(
            "SELECT updated_at FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()[0]
        attachment_id = enqueue_remote_attachment(
            conn,
            message_id,
            provider="telnyx",
            remote_url="https://api.telnyx.com/media/unavailable",
            dedupe_key="telnyx:retry:0",
            max_attempts=2,
        )
        conn.commit()
        conn.close()

        first_attempt_at = time.time() + 1

        def fail_download(_job):
            raise RuntimeError("temporary outage")

        self.assertEqual(
            process_attachment_jobs(limit=1, now=first_attempt_at, downloader=fail_download),
            1,
        )
        conn = connect()
        first_job = conn.execute(
            "SELECT * FROM attachment_ingestion_jobs WHERE attachment_id = ?", (attachment_id,)
        ).fetchone()
        first_attachment = conn.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
        retry_message_updated_at = conn.execute(
            "SELECT updated_at FROM messages WHERE id = ?", (message_id,)
        ).fetchone()[0]
        retry_conversation_updated_at = conn.execute(
            "SELECT updated_at FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()[0]
        self.assertEqual(first_job["status"], "retry")
        self.assertEqual(first_job["attempts"], 1)
        self.assertGreaterEqual(first_job["available_at"], first_attempt_at + 5)
        self.assertEqual(first_attachment["ingestion_status"], "retrying")
        self.assertNotEqual(retry_message_updated_at, original_message_updated_at)
        self.assertNotEqual(retry_conversation_updated_at, original_conversation_updated_at)
        retry_at = float(first_job["available_at"])
        conn.close()

        self.assertEqual(
            process_attachment_jobs(limit=1, now=retry_at - 0.1, downloader=fail_download),
            0,
        )
        self.assertEqual(
            process_attachment_jobs(limit=1, now=retry_at + 0.1, downloader=fail_download),
            1,
        )
        conn = connect()
        final_job = conn.execute(
            "SELECT * FROM attachment_ingestion_jobs WHERE attachment_id = ?", (attachment_id,)
        ).fetchone()
        final_attachment = conn.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
        failed_message_updated_at = conn.execute(
            "SELECT updated_at FROM messages WHERE id = ?", (message_id,)
        ).fetchone()[0]
        failed_conversation_updated_at = conn.execute(
            "SELECT updated_at FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()[0]
        self.assertEqual(final_job["status"], "failed")
        self.assertEqual(final_job["attempts"], 2)
        self.assertEqual(final_attachment["ingestion_status"], "failed")
        self.assertIn("temporary outage", final_attachment["ingestion_error"])
        self.assertNotEqual(failed_message_updated_at, retry_message_updated_at)
        self.assertNotEqual(failed_conversation_updated_at, retry_conversation_updated_at)
        conn.close()

    def test_retry_backoff_starts_when_slow_attempt_finishes(self) -> None:
        _, message_id = self._create_message()
        conn = connect()
        init_db(conn)
        attachment_id = enqueue_remote_attachment(
            conn,
            message_id,
            provider="twilio",
            remote_url="https://api.twilio.com/media/slow-failure",
            dedupe_key="twilio:slow-failure:0",
        )
        conn.commit()
        conn.close()
        started_at = time.time() + 1
        finished_at = started_at + 45

        def slow_failure(_job):
            raise RuntimeError("timeout")

        with patch("texting_app.attachment_ingestion.time.time", side_effect=[started_at, finished_at]):
            self.assertEqual(
                process_attachment_jobs(
                    limit=1,
                    downloader=slow_failure,
                ),
                1,
            )
        conn = connect()
        job = conn.execute(
            "SELECT * FROM attachment_ingestion_jobs WHERE attachment_id = ?", (attachment_id,)
        ).fetchone()
        self.assertGreaterEqual(job["available_at"], finished_at + 5)
        conn.close()

    def test_worker_rejects_oversized_media_before_network_io(self) -> None:
        _, message_id = self._create_message()
        conn = connect()
        init_db(conn)
        attachment_id = enqueue_remote_attachment(
            conn,
            message_id,
            provider="twilio",
            remote_url="https://api.twilio.com/media/too-large",
            size=2 * 1024 * 1024,
            dedupe_key="twilio:too-large:0",
        )
        conn.commit()
        conn.close()
        with (
            patch("texting_app.attachment_ingestion.app_settings.get_int", return_value=1),
            patch("texting_app.attachment_ingestion.urllib.request.build_opener") as build_opener,
        ):
            self.assertEqual(process_attachment_jobs(limit=1, now=time.time() + 1), 1)
        build_opener.assert_not_called()
        conn = connect()
        attachment = conn.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
        self.assertEqual(attachment["ingestion_status"], "failed")
        self.assertIn("download limit", attachment["ingestion_error"])
        conn.close()

    def test_startup_backfill_queues_legacy_remote_attachment(self) -> None:
        _, message_id = self._create_message()
        conn = connect()
        init_db(conn)
        attachment_id = add_attachment(
            conn,
            message_id,
            remote_url="https://api.twilio.com/media/legacy",
            source="twilio",
        )
        conn.commit()
        conn.close()

        self.assertEqual(backfill_attachment_jobs(), 1)
        self.assertEqual(backfill_attachment_jobs(), 0)
        conn = connect()
        attachment = conn.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
        job = conn.execute("SELECT * FROM attachment_ingestion_jobs WHERE attachment_id = ?", (attachment_id,)).fetchone()
        self.assertEqual(attachment["ingestion_status"], "pending")
        self.assertEqual(job["status"], "queued")
        conn.close()

    def test_startup_backfill_recovers_missing_local_file(self) -> None:
        _, message_id = self._create_message()
        conn = connect()
        init_db(conn)
        attachment_id = add_attachment(
            conn,
            message_id,
            local_path="media/deleted.jpg",
            remote_url="https://api.twilio.com/media/deleted",
            source="twilio",
        )
        conn.commit()
        conn.close()

        self.assertEqual(backfill_attachment_jobs(), 1)
        conn = connect()
        attachment = conn.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
        job = conn.execute("SELECT * FROM attachment_ingestion_jobs WHERE attachment_id = ?", (attachment_id,)).fetchone()
        self.assertIsNone(attachment["local_path"])
        self.assertEqual(attachment["ingestion_status"], "pending")
        self.assertEqual(job["status"], "queued")
        conn.close()

    def test_startup_backfill_requeues_exhausted_job(self) -> None:
        _, message_id = self._create_message()
        conn = connect()
        init_db(conn)
        attachment_id = enqueue_remote_attachment(
            conn,
            message_id,
            provider="twilio",
            remote_url="https://api.twilio.com/media/recover-after-restart",
            dedupe_key="twilio:restart:0",
            max_attempts=1,
        )
        conn.commit()
        conn.close()

        def fail_download(_job):
            raise RuntimeError("credentials unavailable")

        self.assertEqual(
            process_attachment_jobs(limit=1, now=time.time() + 1, downloader=fail_download),
            1,
        )
        self.assertEqual(backfill_attachment_jobs(), 1)
        self.assertEqual(backfill_attachment_jobs(), 0)
        conn = connect()
        attachment = conn.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
        job = conn.execute("SELECT * FROM attachment_ingestion_jobs WHERE attachment_id = ?", (attachment_id,)).fetchone()
        self.assertEqual(attachment["ingestion_status"], "pending")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["attempts"], 0)
        conn.close()

    def test_schema_has_attachment_scheduling_and_job_indexes(self) -> None:
        conn = connect()
        init_db(conn)
        attachment_indexes = {row["name"] for row in conn.execute("PRAGMA index_list(attachments)").fetchall()}
        schedule_indexes = {row["name"] for row in conn.execute("PRAGMA index_list(scheduled_messages)").fetchall()}
        job_indexes = {row["name"] for row in conn.execute("PRAGMA index_list(attachment_ingestion_jobs)").fetchall()}
        self.assertIn("idx_attachments_message", attachment_indexes)
        self.assertIn("idx_scheduled_messages_conversation_status_time", schedule_indexes)
        self.assertIn("idx_attachment_ingestion_jobs_ready", job_indexes)
        self.assertIn("idx_attachment_ingestion_jobs_locked", job_indexes)
        conn.close()

    def test_schema_migrates_legacy_attachment_table(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite"
        conn = connect(legacy_path)
        conn.execute(
            """
            CREATE TABLE attachments (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              message_id INTEGER NOT NULL,
              local_path TEXT,
              remote_url TEXT,
              content_type TEXT,
              size INTEGER,
              sha256 TEXT,
              filename TEXT,
              source TEXT NOT NULL DEFAULT 'local'
            )
            """
        )
        conn.commit()
        init_db(conn)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(attachments)").fetchall()}
        self.assertTrue({"ingestion_status", "ingestion_error", "ingestion_updated_at"} <= columns)
        self.assertIsNotNone(
            conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'attachment_ingestion_jobs'"
            ).fetchone()
        )
        conn.close()

    def test_twilio_media_auth_is_limited_to_provider_hosts(self) -> None:
        expected = "Basic " + base64.b64encode(b"AC-test:secret").decode("ascii")
        with patch("texting_app.attachment_ingestion.app_settings.get_value", side_effect=lambda _key, default: default):
            self.assertEqual(
                _provider_headers("https://api.twilio.com/Media/ME123", "twilio")["Authorization"],
                expected,
            )
            self.assertNotIn(
                "Authorization",
                _provider_headers("https://example.com/untrusted", "twilio"),
            )
            self.assertNotIn(
                "Authorization",
                _provider_headers("http://api.twilio.com/Media/ME123", "twilio"),
            )
            original = urllib.request.Request(
                "https://api.twilio.com/Media/ME123",
                headers={"Authorization": expected},
            )
            with patch(
                "texting_app.attachment_ingestion.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
            ):
                external_redirect = _ProviderRedirectHandler("twilio").redirect_request(
                    original,
                    None,
                    302,
                    "Found",
                    {},
                    "https://cdn.example.com/ME123",
                )
            self.assertNotIn("Authorization", external_redirect.headers)

    def test_remote_media_urls_must_resolve_to_public_https_addresses(self) -> None:
        with patch(
            "texting_app.attachment_ingestion.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 8443))],
        ):
            _validate_remote_url("https://media.example:8443/file.jpg")
            with self.assertRaises(AttachmentPermanentError):
                _validate_remote_url("http://media.example/file.jpg")

        private_addresses = [
            (2, 1, 6, "", ("127.0.0.1", 443)),
            (2, 1, 6, "", ("10.0.0.5", 443)),
            (2, 1, 6, "", ("224.0.0.1", 443)),
            (10, 1, 6, "", ("::1", 443, 0, 0)),
            (10, 1, 6, "", ("ff02::1", 443, 0, 0)),
        ]
        for address in private_addresses:
            with (
                self.subTest(address=address[4][0]),
                patch("texting_app.attachment_ingestion.socket.getaddrinfo", return_value=[address]),
                self.assertRaises(AttachmentPermanentError),
            ):
                _validate_remote_url("https://media.example/file.jpg")

    def test_unsigned_provider_webhooks_require_explicit_local_opt_in(self) -> None:
        with (
            patch("texting_app.twilio._auth_token", return_value=""),
            patch("texting_app.twilio.app_settings.get_bool", return_value=False),
        ):
            self.assertFalse(verify_twilio_signature(b"Body=hello", {}, "https://example.test/webhook"))
        with (
            patch("texting_app.twilio._auth_token", return_value=""),
            patch("texting_app.twilio.app_settings.get_bool", return_value=True),
        ):
            self.assertTrue(verify_twilio_signature(b"Body=hello", {}, "https://example.test/webhook"))
        with (
            patch("texting_app.telnyx.app_settings.get_value", return_value=""),
            patch("texting_app.telnyx.app_settings.get_bool", return_value=False),
        ):
            self.assertFalse(verify_telnyx_signature(b"{}", None, None))
        with (
            patch("texting_app.telnyx.app_settings.get_value", return_value=""),
            patch("texting_app.telnyx.app_settings.get_bool", return_value=True),
        ):
            self.assertTrue(verify_telnyx_signature(b"{}", None, None))

    def test_fax_completion_keeps_one_lazy_pdf_attachment(self) -> None:
        _, message_id = self._create_message()
        conn = connect()
        init_db(conn)
        attachment_id = enqueue_remote_attachment(
            conn,
            message_id,
            provider="telnyx",
            remote_url="https://media.telnyx.com/fax.pdf",
            content_type="application/pdf",
            source="telnyx-fax",
            dedupe_key="telnyx:fax:test",
        )
        conn.commit()
        conn.close()

        def downloader(_job):
            config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
            target = config.MEDIA_DIR / f"attachment-{attachment_id}.pdf"
            target.write_bytes(b"%PDF-1.4\n%%EOF\n")
            return DownloadResult(f"media/{target.name}", target.stat().st_size, "a" * 64, "application/pdf")

        self.assertEqual(process_attachment_jobs(limit=1, downloader=downloader), 1)
        conn = connect()
        attachments = conn.execute("SELECT * FROM attachments WHERE message_id = ?", (message_id,)).fetchall()
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["local_path"], f"media/attachment-{attachment_id}.pdf")
        self.assertEqual(attachments[0]["ingestion_status"], "ready")
        conn.close()

    def test_twilio_webhook_store_never_downloads_before_commit(self) -> None:
        conn = connect()
        init_db(conn)
        params = {
            "MessageSid": "SM-async",
            "From": "+15551234567",
            "To": "+15557654321",
            "Body": "photo",
            "NumMedia": "1",
            "MediaUrl0": "https://api.twilio.com/media/ME-async",
            "MediaContentType0": "image/jpeg",
        }
        with (
            patch("texting_app.twilio._notify_incoming_message"),
            patch("texting_app.autoreply.maybe_send_autoreply"),
            patch("urllib.request.urlopen", side_effect=AssertionError("webhook attempted network I/O")),
            patch("urllib.request.build_opener", side_effect=AssertionError("webhook attempted network I/O")),
        ):
            message_id = _store_inbound_message(conn, params)
        self.assertIsNotNone(message_id)
        attachment = conn.execute("SELECT * FROM attachments WHERE message_id = ?", (message_id,)).fetchone()
        job = conn.execute("SELECT * FROM attachment_ingestion_jobs WHERE attachment_id = ?", (attachment["id"],)).fetchone()
        self.assertIsNone(attachment["local_path"])
        self.assertEqual(attachment["ingestion_status"], "pending")
        self.assertEqual(job["status"], "queued")
        conn.close()

    def test_telnyx_webhook_store_never_downloads_before_commit(self) -> None:
        conn = connect()
        init_db(conn)
        event = {
            "data": {
                "id": "event-async",
                "event_type": "message.received",
                "occurred_at": now_est(),
                "payload": {
                    "id": "telnyx-message-async",
                    "direction": "inbound",
                    "from": {"phone_number": "+15551234567"},
                    "to": [{"phone_number": "+15557654321"}],
                    "text": "photo",
                    "media": [
                        {
                            "url": "https://api.telnyx.com/media/async.jpg",
                            "content_type": "image/jpeg",
                            "size": 123,
                        }
                    ],
                },
            }
        }
        with (
            patch("texting_app.telnyx._notify_incoming_message"),
            patch("texting_app.autoreply.maybe_send_autoreply"),
            patch("urllib.request.urlopen", side_effect=AssertionError("webhook attempted network I/O")),
            patch("urllib.request.build_opener", side_effect=AssertionError("webhook attempted network I/O")),
        ):
            message_id = _store_webhook_message(conn, event)
        self.assertIsNotNone(message_id)
        attachment = conn.execute("SELECT * FROM attachments WHERE message_id = ?", (message_id,)).fetchone()
        job = conn.execute("SELECT * FROM attachment_ingestion_jobs WHERE attachment_id = ?", (attachment["id"],)).fetchone()
        self.assertIsNone(attachment["local_path"])
        self.assertEqual(attachment["ingestion_status"], "pending")
        self.assertEqual(job["provider"], "telnyx")
        conn.close()


if __name__ == "__main__":
    unittest.main()
