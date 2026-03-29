# ILUMINATY Python Client

```bash
pip install iluminaty-client
```

```python
from iluminaty_client import Iluminaty

eye = Iluminaty()

# See the screen
snapshot = eye.see()

# Read text (OCR)
text = eye.read()
print(text.text)

# What changed?
diff = eye.what_changed()
print(f"{diff.change_percentage}% changed")

# What is the user doing?
ctx = eye.what_doing()
print(f"Workflow: {ctx.workflow}, Focus: {ctx.focus_level}")

# Mark something on screen
eye.mark(100, 200, text="Bug here", color="#FF0000")

# Ask AI about the screen
answer = eye.ask("gemini", "What error do you see?", api_key="AIza...")
print(answer.text)

# Stream frames
for frame in eye.watch(fps=2):
    print(f"Frame: {frame.width}x{frame.height} at {frame.time_str}")
```
