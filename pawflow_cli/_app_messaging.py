"""PawCode message send / attachments / clipboard."""

import sys
from pathlib import Path

from pawflow_cli.config import save_config
# Split out of pawflow_cli/app.py for the <=800-line rule; composed back into
# PawCode (invariant 2: MRO/shared state).


class _PawCodeMessagingMixin:
    """message send / attachments / clipboard."""

    def _send_message(self, text: str):
        """Send a message to the agent (non-blocking — events rendered by background thread)."""
        # A message only exists inside a conversation — never create one
        # implicitly. Require an explicit /new or /conv first.
        if not self.conversation_id:
            self.renderer.print_error(
                "No active conversation. Use /new to create one or /conv <id> to select one.")
            return
        if not self.selected_agent:
            self.renderer.print_error("No active agent selected. Use /new [agent] or /conv <id> first.")
            return
        # Erase the raw prompt line, replace with styled Panel
        sys.stdout.write("\033[A\033[2K")
        sys.stdout.flush()
        # Show attachment count in user message if any
        attach_info = f" [📎 {len(self._pending_attachments)} file(s)]" if self._pending_attachments else ""
        self.renderer.print_user_message(text + attach_info, self.selected_agent)
        try:
            attachments = self._pending_attachments if self._pending_attachments else None
            self._ensure_sse()
            resp = self.api.send_message(
                message=text,
                conversation_id=self.conversation_id,
                target_agent=self.selected_agent,
                attachments=attachments,
                msg_id=self._new_outgoing_msg_id(),
            )
            if resp.get("error"):
                self.renderer.print_error(resp["error"])
                return
            self._pending_attachments = []  # clear after successful send

            cid = resp.get("conversation_id")
            if cid:
                self.conversation_id = cid
                save_config({"last_conversation_id": cid})

        except PermissionError:
            self.renderer.print_error("Session expired. Run /login to re-authenticate.")
        except Exception as e:
            self.renderer.print_error(f"Send error: {e}")

    def _send_targeted_message(self, text: str, target_agent: str = ""):
        """Send a message to a specific agent without blocking the prompt."""
        if not self.conversation_id:
            self.renderer.print_error(
                "No active conversation. Use /new to create one or /conv <id> to select one.")
            return
        target = target_agent or self.selected_agent
        if not target:
            self.renderer.print_error("No target agent selected")
            return
        sys.stdout.write("\033[A\033[2K")
        sys.stdout.flush()
        attach_info = f" [📎 {len(self._pending_attachments)} file(s)]" if self._pending_attachments else ""
        self.renderer.print_user_message(text + attach_info, target)
        try:
            attachments = self._pending_attachments if self._pending_attachments else None
            self._ensure_sse()
            resp = self.api.send_message(
                message=text,
                conversation_id=self.conversation_id,
                target_agent=target,
                attachments=attachments,
                msg_id=self._new_outgoing_msg_id(),
            )
            if resp.get("error"):
                self.renderer.print_error(resp["error"])
                return
            self._pending_attachments = []
            cid = resp.get("conversation_id")
            if cid:
                self.conversation_id = cid
                save_config({"last_conversation_id": cid})
        except Exception as e:
            self.renderer.print_error(f"Send error: {e}")

    def _upload_file(self, file_path: str):
        """Queue a local file as pending attachment (sent with next message)."""
        import base64
        import mimetypes
        path = Path(file_path)
        if not path.is_file():
            self.renderer.print_error(f"File not found: {file_path}")
            return
        if path.stat().st_size > 10 * 1024 * 1024:
            self.renderer.print_error(f"File too large (max 10MB): {path.name}")
            return
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        self._pending_attachments.append({
            "filename": path.name,
            "mime_type": mime,
            "data": b64,
        })
        n = len(self._pending_attachments)
        self.renderer.print_system(f"📎 {path.name} ({len(data):,} bytes) — {n} file(s) queued. Type message + Enter to send.")

    def _paste_clipboard_image(self):
        """Queue clipboard image as pending attachment."""
        import base64
        try:
            from PIL import ImageGrab
            img = ImageGrab.grabclipboard()
            if img is None:
                self.renderer.print_error("No image in clipboard")
                return
            import io
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            self._pending_attachments.append({
                "filename": "clipboard.png",
                "mime_type": "image/png",
                "data": b64,
            })
            n = len(self._pending_attachments)
            self.renderer.print_system(f"📎 clipboard image ({len(buf.getvalue()):,} bytes) — {n} file(s) queued. Type message + Enter to send.")
        except ImportError:
            self.renderer.print_error("Install Pillow for clipboard support: pip install Pillow")
        except Exception as e:
            self.renderer.print_error(f"Clipboard paste failed: {e}")

    def _clear_attachments(self):
        """Clear pending attachments."""
        self._pending_attachments.clear()
        self.renderer.print_system("Attachments cleared.")

    def _copy_last_message(self, arg: str = ""):
        """Copy last agent response (or Nth) to clipboard."""
        if not self._last_responses:
            self.renderer.print_error("No responses to copy")
            return
        idx = -1
        if arg and arg.isdigit():
            idx = -int(arg) if int(arg) > 0 else -1
        try:
            text = self._last_responses[idx]
        except IndexError:
            self.renderer.print_error(f"Only {len(self._last_responses)} responses available")
            return
        try:
            # Try pyperclip first (cross-platform)
            import pyperclip
            pyperclip.copy(text)
            self.renderer.print_system(f"Copied {len(text):,} chars to clipboard")
        except ImportError:
            # Fallback: platform-specific
            if sys.platform == "win32":
                import subprocess  # nosec B404
                subprocess.run(["clip"], input=text.encode("utf-8"), check=True)  # nosec B603, B607
                self.renderer.print_system(f"Copied {len(text):,} chars to clipboard")
            elif sys.platform == "darwin":
                import subprocess  # nosec B404
                subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)  # nosec B603, B607
                self.renderer.print_system(f"Copied {len(text):,} chars to clipboard")
            else:
                # Linux — try xclip
                try:
                    import subprocess  # nosec B404
                    subprocess.run(["xclip", "-selection", "clipboard"],  # nosec B603, B607
                                   input=text.encode("utf-8"), check=True)
                    self.renderer.print_system(f"Copied {len(text):,} chars to clipboard")
                except Exception:
                    self.renderer.print_error("Install pyperclip or xclip for clipboard support")
        except Exception as e:
            self.renderer.print_error(f"Copy failed: {e}")
