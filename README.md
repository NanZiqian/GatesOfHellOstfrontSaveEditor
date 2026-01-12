# GoH Save Editor (Conquest + Campaign/Mission)

A small Windows GUI save editor for **Call to Arms – Gates of Hell: Ostfront**. Help you win while keeping the achievements!

It supports:

- **Conquest / Dynamic Campaign** `.sav` files (resource points + difficulty/fog + enabled mods list)
- **Singleplayer mission/campaign** `.sav` files (difficulty/fog/mission options + optional player-0 human invincibility)

> Always back up your save before editing.

## Features

### Conquest / Dynamic Campaign saves

Edits the `status` entry inside the `.sav` (the game uses a ZIP-like container):

- `mp` (Manpower)
- `sp` (Support points)
- `ap` (Ammo points)
- `rp` (Research points)
- Difficulty (`easy|normal|hard|heroic`)
- Fog of war (`fog_realistic|fog_off`)
- Shows enabled `"mod_..."` entries and lets you remove them

### Mission / Campaign saves

Edits the `status` / `mission.scn` inside the `.sav`:

- Difficulty (`easy|normal|hard|heroic`)
- Fog of war (`fog_realistic|fog_off`)
- `mission_options` (enter without quotes; the editor writes `{mission_options "..."}` safely)
- **Recommend**: **Make Player 0 humans invincible** (adds `{Impregnability full}` to Player 0 `Human` blocks)

## Where are saves stored?

Common locations mentioned by players:

- Conquest/Dynamic Campaign: `Documents\My Games\Gates of Hell\profiles\<id>\campaign\`
- Mission/campaign saves: `Documents\My Games\Gates of Hell\profiles\<id>\save\`

## How to use

### Option A: Edit a `.sav` directly (recommended)

1. Run `mod_goh.exe`.
2. Click **Choose your save**.
3. Select the `.sav` file.
4. Change values.
5. Click **Save changes**.

If Windows blocks overwriting the original `.sav` (game open / Steam Cloud / read-only), the editor will ask you to **Save As** and create an edited copy.

### Option B (not recommended): Edit an extracted save folder

1. Extract the `.sav` with 7-Zip/WinRAR into a folder.
2. Run `mod_goh.exe`.
3. Click **Choose extracted folder**.
4. Select that extracted folder.

This edits the files **in-place inside the folder**. It does not automatically repack them into a `.sav`.

## Safety notes

- Make a backup copy of your `.sav` before editing.
- Close the game while editing saves.
- Steam Cloud may overwrite local files; consider disabling it while testing edits.

## Build the executable (developers)

This project uses **PyInstaller**.

### Build

From the project folder:

```powershell
python -m pip install --upgrade pyinstaller
pyinstaller --clean --noconfirm mod_goh.spec
```

Output:

- `dist\mod_goh.exe`

## Repository structure

- `mod_goh.py` – main GUI editor
- `mod_goh.spec` – PyInstaller build spec
- `dist/` – built exe output (created when you build)
- `build/` – PyInstaller build cache (created when you build)

## Known limitations

- The mission/campaign invincibility option currently targets **Humans only** (not vehicles).
- Save formats can change with game updates; if a `.sav` does not contain expected files, the editor will refuse to edit it.

## Disclaimer

This is an unofficial tool. Use at your own risk.
