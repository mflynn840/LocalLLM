#!/usr/bin/env python3
"""Install the local execution switch and safe shared-workspace behavior."""
from pathlib import Path
import re
import shutil

SITE = Path("/usr/local/lib/python3.13/site-packages/openhands")
APP = Path("/opt/agent-canvas")
HERE = Path(__file__).parent

launch = SITE / "agent_server/launch.py"
source = launch.read_text()
if "apply_terminal_mode" not in source:
    if "def launch_source(" not in source or "def request_secrets(" not in source:
        raise SystemExit("Unsupported OpenHands launch.py layout")
    source = source.replace("def launch_source(", "def _launch_source(", 1)
    marker = "\ndef request_secrets("
    wrapper = '''
def launch_source(request, stores, cipher):
    from .local_execution import apply_launch_overrides
    return apply_launch_overrides(_launch_source(request, stores, cipher), request)

'''
    source = source.replace(marker, "\n" + wrapper + "def request_secrets(", 1)
    launch.write_text(source)
shutil.copy2(HERE / "local_execution.py", SITE / "agent_server/local_execution.py")

editor = SITE / "tools/file_editor/editor.py"
source = editor.read_text()
before = '''            # Preserve the original file's permission bits when it already exists.
            if path.exists():
                os.chmod(tmp_path, os.stat(path).st_mode & 0o7777)
            Path.replace(tmp_path, path)'''
after = '''            parent_stat = path.parent.stat()
            mode = (os.stat(path).st_mode & 0o7777) if path.exists() else 0o664
            if parent_stat.st_mode & 0o020:
                mode |= 0o060
            try:
                os.chown(tmp_path, parent_stat.st_uid, parent_stat.st_gid)
            except PermissionError:
                pass
            os.chmod(tmp_path, mode)
            Path.replace(tmp_path, path)'''
if before in source:
    source = source.replace(before, after, 1)
elif after not in source:
    raise SystemExit("Unsupported OpenHands FileEditor atomic-write layout")
source = re.sub(
    r'(if command == "create" and path\.exists\(\):\s+if path\.is_dir\(\):\s+raise EditorToolParameterInvalidError\(\s+"path",\s+str\(path\),\s+)(f"Path is an existing directory:[^\n]+)',
    r'\1f"Path is an existing directory: {path}. `create` only creates files. Use create_directory or mkdir -p, then create a file inside it.",',
    source,
    count=1,
)
editor.write_text(source)

# OpenHands' own SDK strips its standard session keys before launching shell
# commands. Extend that allowlist for credentials injected by this Compose app.
env_helpers = SITE / "sdk/utils/command.py"
source = env_helpers.read_text()
anchor = '        "SESSION_API_KEY",\n'
if anchor not in source:
    raise SystemExit("Unsupported OpenHands sanitized environment layout")
for key in (
    "LLM_API_KEY", "LOCAL_BACKEND_API_KEY", "VITE_POSTHOG_API_KEY",
    "LITELLM_MASTER_KEY", "LLAMA_API_KEY", "OPENAI_API_KEY",
    "WEB_LOADER_API_KEY", "SEARXNG_SECRET", "WEBUI_SECRET_KEY",
    "WEBUI_ADMIN_PASSWORD",
):
    entry = f'        "{key}",\n'
    if entry not in source:
        source = source.replace(anchor, anchor + entry, 1)
env_helpers.write_text(source)

frontend = APP / "frontend"
shutil.copy2(HERE / "frontend/local-execution-mode.js", frontend / "local-execution-mode.js")
shutil.copy2(HERE / "frontend/local-execution-mode.css", frontend / "local-execution-mode.css")
shutil.copy2(HERE / "frontend/local-model-selector.js", frontend / "local-model-selector.js")
index = frontend / "index.html"
html = index.read_text()
css = '<link rel="stylesheet" href="/canvas/local-execution-mode.css">'
js = '<script defer src="/canvas/local-execution-mode.js"></script>'
model_js = '<script defer src="/canvas/local-model-selector.js"></script>'
if "local-execution-mode.js" not in html:
    html = html.replace("</head>", css + js + model_js + "</head>", 1)
    index.write_text(html)
elif "local-model-selector.js" not in html:
    html = html.replace("</head>", model_js + "</head>", 1)
    index.write_text(html)
print("Installed OpenHands Docker/My PC execution switch and shared-file fixes.")
