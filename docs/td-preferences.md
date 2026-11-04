# TouchDesigner Preferences

TouchDesigner keeps its preferences in one file per machine, `pref.txt`, shared by every TD build and every project. A few of them matter a lot on a show machine: a minimized window shouldn't stop the timeline, and a stray key shouldn't stop playback. ALPACKA can keep those set for you.

ALPACKA never replaces `pref.txt`. It only sets the preferences you tick, leaves every other line as it was, and can put each one back the way it found it.

## The first-run prompt

The first time ALPACKA starts with TouchDesigner installed, it lists the preferences it recommends. Each row shows the value in your `pref.txt` **now** next to the value **ALPACKA sets**, so you can see exactly what would change.

- **Apply Selected** — ALPACKA manages the ticked rows. The unticked ones are left alone and not asked about again.
- **Ignore** — nothing changes, and none of these are asked about again.
- **Closing the dialog** — nothing is decided; it asks again next time.

When a later version of ALPACKA recommends a new preference, the prompt comes back with just that one.

The prompt appears after any projects set to launch on startup have launched, so it never holds up an unattended machine.

## Recommended preferences

| Preference | ALPACKA sets | Why |
|---|---|---|
| Stop Playing when Minimized (`general.stopplayingwhenminimized`) | Off | Minimizing the window shouldn't stop the show. |
| Enable Playbar Shortcuts (`general.playbarshortcuts`) | Off | A stray key shouldn't stop the timeline. |
| Show Startup Errors (`general.showstartuperrors`) | None | No warnings or errors popup sitting on an unattended screen. None is already TD's default, so this usually shows "Already set"; ALPACKA puts it back if someone picks Warnings or Errors. |
| Enable Warning Sounds (`general.sounds`) | Off | Keeps TD's warning beeps out of the show's audio. |
| Prompt to Save on Exit (`general.saveprompt`) | Off | A save prompt can hold up a scheduled restart. Unticked by default: with it off, unsaved changes are lost when TD closes. |
| Hide Splash Screen (`general.hidesplashscreen`) | On | Keeps the splash screen off the projector while TD starts. Only works with a Commercial or Pro licence. Unticked by default. |
| DAT text editor (`dats.texteditor`) | VS Code, if found | Edit DATs in VS Code. Unticked by default. A project's own editor setting (Edit Details → TouchDesigner Launch) may take priority. |

## When ALPACKA writes `pref.txt`

- **Before every TD launch** — from the library, auto-launch at startup, and crash relaunch.
- **When you click Apply or Revert All** in Settings.

It skips the write when nothing needs to change, and **never writes while any TouchDesigner is running** — including ones ALPACKA didn't start — because a running TD rewrites the whole file from memory when its Preferences dialog is closed, which would undo ALPACKA's change. The change waits for the next launch with no other TD open.

If a managed preference was changed outside ALPACKA (for example in TD's own Preferences dialog), ALPACKA sets it back at the next launch and shows a banner at the top of the window saying what changed and what it set back.

## Settings

**Settings → TouchDesigner Preferences** shows the same table as the prompt, plus:

- the path to `pref.txt` and an **Open Folder** button;
- the result of the last attempt — applied, up to date, waiting because TD was running, or an error;
- a note on any managed preference whose value differs from what ALPACKA set.

Tick or untick rows and click **Apply**. Unticking a preference puts back the value it had before ALPACKA changed it (or removes the line if it wasn't there). **Revert All** does that for every managed preference.

> [!NOTE]
> Before it first changes `pref.txt`, ALPACKA saves a copy next to it as `pref.txt.alpacka-backup`. Revert restores just the managed preferences, so changes you made to other preferences since then are kept.

## Python packages path

> [!WARNING]
> If TD's **Python 64-bit Module Path** (`general.pythonpackages64`) is set, packages from that folder can override a project's own venv. ALPACKA shows a warning but never changes it — clear it in TD's Preferences if projects pick up the wrong package versions.

## Where things are stored

See [Configuration](configuration.md#touchdesigners-preftxt) for the `pref.txt` location and the `td_prefs.json` file where ALPACKA keeps your choices.
