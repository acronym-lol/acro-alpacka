# Third-Party Notices

ALPACKA is copyright © 2026 Acronym and released under the MIT License (see [LICENSE](LICENSE)). It is built with, and its packaged builds include, the third-party software and artwork below. Full licence texts are in the [`licenses/`](licenses) folder, which also ships next to `ALPACKA.exe` (Windows) and inside `ALPACKA.app/Contents/Resources` (macOS). Licence files marked *(added at build time)* are copied from the installed packages when the app is built.

## Libraries in the packaged app

| Component | Licence | Text in `licenses/` |
|---|---|---|
| [Qt for Python](https://www.qt.io/qt-for-python) — PySide6 and Shiboken6, and the Qt libraries they ship with (The Qt Company) | LGPL-3.0 (also offered under GPL-2.0 / GPL-3.0) | `LGPL-3.0.txt`, `GPL-3.0.txt` |
| [psutil](https://github.com/giampaolo/psutil) | BSD-3-Clause | `psutil-LICENSE` *(added at build time)* |
| [PyYAML](https://github.com/yaml/pyyaml) | MIT | `PyYAML-LICENSE` *(added at build time)* |
| [ruamel.yaml](https://sourceforge.net/projects/ruamel-yaml/) | MIT | `ruamel.yaml-LICENSE` *(added at build time)* |
| [Python](https://www.python.org/) (Python Software Foundation) | PSF License Agreement | `Python-LICENSE.txt` *(added at build time)* |
| [PyInstaller](https://pyinstaller.org/) bootloader | GPL-2.0 with the PyInstaller bootloader exception, which allows distributing the bundled app under its own licence | `pyinstaller-COPYING.txt` *(added at build time)* |

### Qt and the LGPL

ALPACKA uses Qt and PySide6 under the GNU Lesser General Public License v3. They are included as separate, unmodified libraries (the `PySide6` and `shiboken6` folders and the Qt libraries inside them), so you can replace them with your own build of the same version. Their source code is available from The Qt Company at [code.qt.io](https://code.qt.io/) and [download.qt.io](https://download.qt.io/official_releases/QtForPython/). ALPACKA's own source is at [github.com/acronym-lol/acro-alpacka](https://github.com/acronym-lol/acro-alpacka).

## Icons

- **[Lucide](https://lucide.dev/)** — ISC License; some Lucide icons derive from [Feather](https://feathericons.com/) (MIT). Text: `Lucide-LICENSE.txt`. ALPACKA's copies are recoloured, and a few are adjusted.
- **Brand logos**, used only to label the tools ALPACKA works with. They remain trademarks of their owners, and their use doesn't imply endorsement:
  - Docker logo — from [Simple Icons](https://simpleicons.org/) (CC0). Docker is a trademark of Docker, Inc.
  - Node.js logo — from Simple Icons (CC0). Node.js is a trademark of the OpenJS Foundation.
  - Python logo — from Wikimedia Commons (public domain). Python is a trademark of the Python Software Foundation.
  - GitHub mark — GitHub is a trademark of GitHub, Inc.
  - Visual Studio Code logo — Visual Studio Code is a trademark of Microsoft Corporation.
  - TouchDesigner logo — TouchDesigner is a trademark of Derivative Inc.

## Project templates

The starter files in `src/alpacka/resources/templates/` are part of ALPACKA (MIT). Projects you create from them are yours, under whatever licence you choose.
