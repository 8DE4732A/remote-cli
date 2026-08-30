---
name: remote-cli
description: >-
  Operate shared remote SSH and terminal sessions with human co-pilots using remote-cli.
  Use when the user provides a session ID to perform remote server operations, deployments,
  debugging, inspecting terminal outputs, or sending inputs to active SSH sessions.
---

# Remote CLI Agent Skill (`remote-cli`)

`remote-cli` enables an AI Agent to co-pilot an active SSH or terminal session with a human user. The human logs into the server (handling passwords, 2FA, bastion hosts, and SSH keys) and provides a `session-id` to the Agent. The Agent can then execute commands, inspect terminal output, send keystrokes, and read screen state while the human observes the operations in real-time in their terminal window.

---

## 1. Installation

If `remote-cli` is not already installed in the environment:

```bash
# Recommended installation via uv tool:
uv tool install remote-cli

# Or via pip:
pip install remote-cli
```

*(If running from local source repository during development: `uv run remote-cli ...`)*

---

## 2. Typical Interaction Workflow

```text
1. User logs into remote server:
   $ remote-cli ssh user@remote-host
   -> Outputs Session ID: s_7f8a9b2c

2. User gives Session ID to Agent:
   "I've logged in. The session ID is s_7f8a9b2c. Please check disk space and Docker containers."

3. Agent runs commands via remote-cli:
   $ remote-cli exec s_7f8a9b2c "df -h"
   $ remote-cli exec s_7f8a9b2c "docker ps"

4. Human sees output in real time in their terminal and can intervene anytime.
```

---

## 3. Command Reference for Agents

### 3.1. Execute Commands (`remote-cli exec`)

Executes a command inside the remote shell, captures stdout, and returns the exit code.

```bash
# Standard execution (prints output; returns remote exit code)
remote-cli exec <session-id> "<command>"

# Example:
remote-cli exec s_7f8a9b2c "systemctl status nginx"

# Structured JSON output (returns JSON object with exit_code, output, duration, timed_out)
remote-cli exec s_7f8a9b2c "uname -a" --json

# Custom timeout (default: 30s)
remote-cli exec s_7f8a9b2c "sleep 5 && echo done" --timeout 60
```

> **JSON Output Format**:
> ```json
> {
>   "session_id": "s_7f8a9b2c",
>   "command": "uname -a",
>   "exit_code": 0,
>   "output": "Linux server 5.15.0-88-generic ...",
>   "duration": 0.045,
>   "timed_out": false
> }
> ```

---

### 3.2. Inspect 2D Screen Snapshot (`remote-cli snapshot`)

Captures the rendered 2D terminal screen state (ANSI escapes rendered into clean text lines).
Use this when you need to see what is currently visible on the user's screen (e.g. interactive prompts, curses/ncurses UIs, `top`, menus).

```bash
remote-cli snapshot <session-id>
```

---

### 3.3. Send Interactive Input & Control Keys (`remote-cli send`)

Sends raw text, keystrokes, or control characters into the active session. Useful when answering interactive prompts (like `y/n`, sudo password, confirmation dialogues) or interrupting long processes.

```bash
# Answer confirmation prompt (appends newline by default):
remote-cli send <session-id> "y"

# Send text without newline:
remote-cli send <session-id> "some-text" --no-newline

# Send Ctrl+C (SIGINT) to interrupt a process:
remote-cli send <session-id> --ctrl-c

# Send Ctrl+D (EOF):
remote-cli send <session-id> --ctrl-d
```

---

### 3.4. View Recent Scrollback Logs (`remote-cli logs`)

Retrieves the recent stream output buffer.

```bash
# Get last 100 lines (default)
remote-cli logs <session-id>

# Get last N lines
remote-cli logs <session-id> --lines 50
```

---

### 3.5. Transfer Files & Directories (`remote-cli cp` / `upload` / `download`)

Seamlessly transfers files and directories between the local machine and the remote server over the active session without requiring SFTP configuration or opening extra ports.

```bash
# Docker-style copy syntax:
# Upload local file/dir to remote server:
remote-cli cp ./dist/app.tar.gz <session-id>:/opt/app/
remote-cli cp ./config/ <session-id>:/opt/config/

# Download remote file/dir to local machine:
remote-cli cp <session-id>:/var/log/nginx/error.log ./logs/
remote-cli cp <session-id>:/var/log/nginx/ ./local_nginx_logs/

# Direct upload/download commands:
remote-cli upload <session-id> <local-path> <remote-path>
remote-cli download <session-id> <remote-path> <local-path>
```

---

### 3.6. List Active Sessions (`remote-cli session list` / `remote-cli ls`)

Discovers active and recent sessions.

```bash
remote-cli ls
```


---

## 4. Agent Guidelines & Best Practices

1. **Check Session Status**:
   - If the user provided a session ID, you can verify it exists with `remote-cli ls` or directly execute a probe command like `remote-cli exec <session-id> "echo ok"`.
2. **Handle Exit Codes**:
   - Always check the exit code. If `exit_code != 0`, analyze the error output before proceeding with dependent operations.
3. **Interactive Prompts**:
   - If a command blocks or you suspect an interactive prompt (e.g., `Do you want to continue? [Y/n]`), use `remote-cli snapshot <session-id>` to read the screen, and then use `remote-cli send <session-id> "y"` to reply.
4. **Transparent Communication**:
   - Inform the user of the operations you are performing. Remember that the user can see your commands and their outputs on their screen in real time!
