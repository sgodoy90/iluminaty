# ILUMINATY Node.js Client

```bash
npm install iluminaty
```

```typescript
import { Iluminaty } from 'iluminaty';

const eye = new Iluminaty();

// See the screen
const snapshot = await eye.see();
console.log(snapshot.ai_prompt);

// Read text (OCR)
const text = await eye.read();
console.log(text.text);

// What changed?
const diff = await eye.whatChanged();
console.log(`${diff.change_percentage}% changed`);

// What is the user doing?
const ctx = await eye.whatDoing();
console.log(`Workflow: ${ctx.workflow}, Focus: ${ctx.is_focused ? 'HIGH' : 'LOW'}`);

// Mark something
await eye.mark(100, 200, { text: 'Bug here', color: '#FF0000' });

// Ask AI
const answer = await eye.ask('gemini', 'What error?', 'AIza...');
console.log(answer.text);

// Stream frames
const stop = eye.watch((frame) => {
  console.log(`Frame: ${frame.width}x${frame.height}`);
}, 2); // 2 fps

// Stop streaming after 10 seconds
setTimeout(stop, 10000);
```
