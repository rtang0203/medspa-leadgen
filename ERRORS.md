# Errors Log

This file documents failed attempts, what worked instead, and notes for next time.

## 2026-06-25: load_dotenv not loading .env values
- **What didn't work:** `load_dotenv()` with default settings. VS Code's `python.terminal.useEnvFile` pre-sets env vars in the shell (even as empty strings), and `load_dotenv()` refuses to override existing env vars.
- **What worked at the time:** `load_dotenv(override=True)` — forces .env values to win over shell env.
- **Update (2026-06-28):** User reverted to plain `load_dotenv()`. The VS Code env issue was resolved separately. Default dotenv behavior is preferred.
- **Note for next time:** If .env values appear to be ignored, check `os.environ` directly before dotenv loads. Fix the upstream source (VS Code settings) rather than using `override=True`.
