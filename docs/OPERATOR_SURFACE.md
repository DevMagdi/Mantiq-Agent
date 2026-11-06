# Operator surface

The procurement operator console is a separate, publishable surface. It is not a retint of `frontend/`.

## Why the navy theme stays

`frontend/src/styles/tokens.test.ts` locks one theme: `color-scheme: dark`, navy `#16283f`, gold `#c9a227`, and WCAG AA pairs written for dark surfaces. Semantic inks (`--ok-ink`, `--warn-ink`, `--danger-ink`, `--info-ink`) are lightened for navy. Swapping those variables for a cream palette fails the brand test and would ship low-contrast status text across the existing screens. That change needs a screen-by-screen pass, not a token overwrite on main.

## The surface that is live

The operator console follows a Claude-like calm: warm paper `#faf9f5`, ink `#141413`, clay `#c96442` used as a mark and a best-value line, black primary actions, Source Serif for the question and IBM Plex Sans / IBM Plex Sans Arabic for UI. Motion is a short rise (opacity, 12px, slight blur), press scale `0.96`, and a three-dot thinking state. `prefers-reduced-motion` collapses it.

Arabic is the default, with an English toggle and logical direction. The agent drafts a quotable request from Arabic or English text. It does not award or pay. A person sends the landed-cost winner into an approval, then approves or rejects it.

## Load check

A burst of 320 document requests across the eight routes returned no failures. The production bundle rendered the same document text as the dev session, with no console errors and no horizontal overflow at 1280 and 390.
