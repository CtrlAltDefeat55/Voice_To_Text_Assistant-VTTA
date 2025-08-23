## SECURITY.md

```markdown
# Security Policy

## Support & updates
This is a small hobby project. Security updates are **best‑effort**.

## Reporting a vulnerability
Open an issue and include the word **SECURITY** in the title. Avoid posting sensitive proof‑of‑concept details publicly; keep reports minimal.

## Risk & behavior disclaimers
This app:
- **Records audio** from your microphone while you press a hotkey.
- Can **paste to your active window** using clipboard/keyboard automation (optional).
- Can **open a web browser** to a search page using Playwright.
- Can **store transcription history locally** in SQLite if you enable it.
- **Voice command keywords** (“start dictation”, “stop dictation”, “search now”) use Google’s online recognizer (network call).

Recommendations:
- Keep “Record History” **off** if you don’t want local text saved.
- Verify the active window before enabling “Write to cursor.”
- Review your OS microphone permissions.
- If you prefer offline‑only behavior, disable voice commands by removing/commenting the `start_voice_command_listener()` call in `Voice_To_Text_Assistant.py`.

**Use at your own risk.** The maintainers are **not responsible** for any data loss or damage.