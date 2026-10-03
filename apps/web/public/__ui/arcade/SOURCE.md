# LittleJS Arcade test copy

Source: https://github.com/KilledByAPixel/LittleJSArcade

Pinned commit: `140e4d511e0001052133fdadb61cb63b85577165`

Included original games: `matchThree`, `zoomi`, `bobblePop`, `pegball`, `brickout`, `pinball`.
Included shared runtime: selected files from `templates/` and `dist/`.

Copyright (c) 2026 Frank Force. MIT license is preserved in `LICENSE`.
The six game HTML files and shared menu template contain Chinese UI-text changes only.
The engine and binary runtime remain upstream copies. `templates/workbenchBridge.js` is
local integration code for pausing gameplay and audio when the game is minimized.
The iframe is sandboxed without same-origin access. This keeps the app login token inaccessible
to game scripts; upstream localStorage-based scores and settings do not persist.
