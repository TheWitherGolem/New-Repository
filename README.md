# ⚙ Omnissiah Academy — AdMech & 40k 11th Edition Trainer

A self-contained, interactive learning app that teaches the Warhammer 40,000
**11th edition** core rules through the lens of the **Adeptus Mechanicus** —
including what meta lists are (and why they win), the strategic philosophy of
**The Art of War** coaching studio's AdMech specialist Richard Siegler, and a
practical Turn 1 playbook.

## Running it

No build step, no server, no dependencies. Just open the file:

```
open index.html        # macOS
xdg-open index.html    # Linux
# or double-click it, or drag it into any browser
```

Quiz progress is saved in your browser's localStorage (per-browser, per-machine).

## What's inside

| Section | Modules |
|---|---|
| **Core Rules** | Reading stats & datasheets (with an interactive mathhammer calculator) · Turn structure & phases · 11th edition's big changes · CP & stratagems |
| **Your Army** | AdMech identity & the Doctrina Imperatives army rule · Key units by battlefield role · Detachments & the Detachment Points system |
| **Competitive Play** | What "meta lists" are & why · The Art of War philosophy · The Turn 1 playbook |
| **Reference** | Glossary & further reading |

Each teaching module ends with a quiz (75% to pass, ✓ tracked in the sidebar).

## Accuracy & disclaimers

- Content is a **snapshot of August 2026** (early 11th edition, which launched
  20 June 2026). Balance dataslates and FAQs will drift; the app teaches
  concepts and decision frameworks, and repeatedly points you to the official
  free Core Rules PDF, the AdMech Faction Pack, and the Warhammer 40,000 app
  for binding wording.
- All rules content is **paraphrased for learning** — no rules text is
  reproduced verbatim.
- Art of War material is **summarized with attribution** from their public
  articles, interviews, and course descriptions. Their actual coaching content
  lives at [theartofwar40k.com](https://theartofwar40k.com) and on their
  YouTube channel — this app is a study companion, not a substitute.
- Fan-made and unaffiliated: Warhammer 40,000 and Adeptus Mechanicus are
  trademarks of Games Workshop Ltd.

## Development notes

The whole app is one `index.html`: lesson content and quizzes are plain data
structures at the top of the inline script, so adding a module means appending
to `LESSONS` (and optionally `QUIZZES`) — the sidebar, pager, quiz engine, and
progress tracking pick it up automatically.
