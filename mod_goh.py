import zipfile
from tkinter import *
from tkinter import filedialog
from tkinter import messagebox
import re
import os
import webbrowser
import tempfile
import shutil
root = Tk()
root.title('Save Editor')
helpLabel = Label(root,
                  text=(
                      "This editor supports two save types:\n"
                      "1) Conquest/Dynamic Campaign (.sav): edits mp/sp/ap/rp + difficulty/fog + active mods.\n"
                      "2) Singleplayer mission/campaign saves (.sav): edits difficulty + fog_of_war + mission options\n"
                      "   (optional: make Player 0 units invincible).\n\n"
                      "Common locations:\n"
                      "- Conquest/Dynamic Campaign: Documents\\My Games\\Gates of Hell\\profiles\\<id>\\campaign\\\n"
                      "- Mission/campaign saves:   Documents\\My Games\\Gates of Hell\\profiles\\<id>\\save\\"
                  ))
helpLabel.grid(row=1, column=1)
mainSaveFile = "./status"
secondarySaveFile = "./campaign.scn"

_work_dir = None


def _cleanup_workdir():
    global _work_dir
    if _work_dir and os.path.isdir(_work_dir):
        shutil.rmtree(_work_dir, ignore_errors=True)
    _work_dir = None


def _find_file_by_basename(root_dir: str, filename: str):
    for base, _dirs, files in os.walk(root_dir):
        if filename in files:
            return os.path.join(base, filename)
    return None


def _read_text(path: str) -> str:
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()


def _write_text(path: str, text: str) -> None:
    with open(path, 'w', encoding='utf-8', errors='ignore', newline='') as f:
        f.write(text)


def _repack_save_to_path(original_save_path: str, extracted_root: str, output_save_path: str) -> None:
    with zipfile.ZipFile(original_save_path, 'r') as original_zip, zipfile.ZipFile(
        output_save_path, 'w', compression=zipfile.ZIP_DEFLATED
    ) as out_zip:
        for info in original_zip.infolist():
            if info.is_dir():
                continue
            extracted_path = os.path.join(extracted_root, info.filename)
            if os.path.isfile(extracted_path):
                out_zip.write(extracted_path, arcname=info.filename)
            else:
                out_zip.writestr(info, original_zip.read(info.filename))


def _repack_save_preserving_entries(original_save_path: str, extracted_root: str) -> None:
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix='goh_save_', suffix='.sav')
        os.close(fd)
        _repack_save_to_path(original_save_path, extracted_root, tmp_path)
        os.replace(tmp_path, original_save_path)
        tmp_path = None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _detect_save_kind(status_text: str) -> str:
    """Return 'conquest' or 'mission'."""
    # Conquest/Dynamic Campaign saves typically contain resources in the status file.
    if re.search(r"\{mp\s+\d", status_text) or re.search(r"\{sp\s+\d", status_text):
        return 'conquest'
    # Mission/campaign saves (singleplayer) usually have a saveinfo/manager block.
    if '{saveinfo' in status_text and '{manager' in status_text:
        return 'mission'
    return 'unknown'


def _get_line_ending(sample_text: str) -> str:
    return '\r\n' if '\r\n' in sample_text else '\n'


def _sanitize_status_string_value(value: str) -> str:
    """Sanitize user input to a single-line value safe for GoH .scn/.status parsers."""
    v = (value or '').strip()
    # Remove newlines and braces that would break block structure.
    v = v.replace('\r', ' ').replace('\n', ' ')
    v = v.replace('{', '').replace('}', '')

    # If user typed quoted/escaped quoted content, normalize to raw token.
    # Examples to handle:
    #   doctrine_offensive
    #   "doctrine_offensive"
    #   "\"doctrine_offensive\""
    if v.startswith('"') and v.endswith('"') and len(v) >= 2:
        v = v[1:-1].strip()
    # Remove any remaining quotes/backslashes.
    v = v.replace('\\', '').replace('"', '').strip()
    # Collapse repeated whitespace.
    v = re.sub(r"\s+", " ", v)
    return v


def _replace_status_line(status_text: str, key: str, new_value_rendered: str) -> str:
    """Replace a single-line {key ...} entry preserving indentation and line endings."""
    lines = status_text.splitlines(keepends=True)
    out_lines: list[str] = []
    replaced = False
    for line in lines:
        m = re.match(rf"^(\s*)\{{{re.escape(key)}\b.*\}}(\s*)$", line.rstrip('\r\n'))
        if (not replaced) and m:
            indent = m.group(1)
            newline = '\r\n' if line.endswith('\r\n') else '\n'
            out_lines.append(f"{indent}{{{key} {new_value_rendered}}}{newline}")
            replaced = True
        else:
            out_lines.append(line)
    return ''.join(out_lines)


def _patch_status_mission(status_text: str, difficulty: str | None, fog_of_war: str | None, mission_options: str | None) -> str:
    out = status_text
    if difficulty:
        out = _replace_status_line(out, 'difficulty', difficulty)
    if fog_of_war:
        # Mission saves tend to use fog_of_war; keep fogofwar as fallback.
        if re.search(r"^\s*\{fog_of_war\b", out, flags=re.MULTILINE):
            out = _replace_status_line(out, 'fog_of_war', fog_of_war)
        elif re.search(r"^\s*\{fogofwar\b", out, flags=re.MULTILINE):
            out = _replace_status_line(out, 'fogofwar', fog_of_war)

    if mission_options is not None:
        safe_opt = _sanitize_status_string_value(mission_options)
        if safe_opt:
            # Render with quotes exactly once: {mission_options "doctrine_offensive"}
            rendered = f'"{safe_opt}"'
            out = _replace_status_line(out, 'mission_options', rendered)
    return out


def _make_player0_humans_invincible(mission_text: str) -> tuple[str, int]:
    """Insert {Impregnability full} into Human blocks that contain {Player 0} and do not already have Impregnability.

    This is a conservative, line-based brace counter that only targets top-level Human entities.
    """
    newline = _get_line_ending(mission_text)
    lines = mission_text.splitlines(keepends=True)

    def brace_delta(s: str) -> int:
        return s.count('{') - s.count('}')

    out_lines: list[str] = []
    i = 0
    changes = 0
    while i < len(lines):
        line = lines[i]
        # Top-level Human entity in mission.scn usually starts with one tab.
        if re.match(r"^\t\{Human\b", line):
            block: list[str] = [line]
            level = brace_delta(line)
            i += 1
            while i < len(lines) and level > 0:
                block.append(lines[i])
                level += brace_delta(lines[i])
                i += 1

            has_player0 = any(re.match(r"^\t\t\{Player\s+0\}\s*$", b.rstrip('\r\n')) for b in block)
            has_impreg = any(re.match(r"^\t\t\{Impregnability\b", b) for b in block)

            if has_player0 and not has_impreg:
                # Insert right after the Player 0 line.
                inserted = False
                new_block: list[str] = []
                for b in block:
                    new_block.append(b)
                    if (not inserted) and re.match(r"^\t\t\{Player\s+0\}\s*$", b.rstrip('\r\n')):
                        new_block.append("\t\t{Impregnability full}" + newline)
                        inserted = True
                        changes += 1
                block = new_block

            out_lines.extend(block)
            continue

        out_lines.append(line)
        i += 1

    return ''.join(out_lines), changes


def _open_mission_editor(save_path: str, extracted_root: str, status_path: str, mission_path: str):
    status_text = _read_text(status_path)

    origin_match = re.search(r"\{origin\s+\"([^\"]+)\"\}", status_text)
    mission_opt_match = re.search(r"\{mission_options\s+\"([^\"]*)\"\}", status_text)
    diff_match = re.search(r"\{difficulty\s+(easy|normal|hard|heroic)\}", status_text)
    fog_match = re.search(r"\{fog_of_war\s+([^\}]+)\}", status_text)

    origin_value = origin_match.group(1) if origin_match else ''
    current_mission_options = mission_opt_match.group(1) if mission_opt_match else ''
    current_diff = diff_match.group(1) if diff_match else None
    current_fog = fog_match.group(1).strip() if fog_match else None

    edit_window = Toplevel(root)
    edit_window.title('Editing mission/campaign save')
    edit_window.minsize(720, 420)
    root.withdraw()

    Label(edit_window, text='Mission/campaign save editing').grid(row=1, column=1, sticky='w')
    Label(edit_window, text=f'Origin: {origin_value}').grid(row=2, column=1, columnspan=3, sticky='w')

    # Difficulty
    Label(edit_window, text='Difficulty').grid(row=3, column=1, sticky='w')
    diff_var = StringVar(value=current_diff or '')
    Radiobutton(edit_window, text='Easy', variable=diff_var, value='easy').grid(row=4, column=1, sticky='w')
    Radiobutton(edit_window, text='Normal', variable=diff_var, value='normal').grid(row=5, column=1, sticky='w')
    Radiobutton(edit_window, text='Hard', variable=diff_var, value='hard').grid(row=6, column=1, sticky='w')
    Radiobutton(edit_window, text='Heroic', variable=diff_var, value='heroic').grid(row=7, column=1, sticky='w')

    # Fog of war
    Label(edit_window, text='Fog of war').grid(row=3, column=2, sticky='w')
    fog_var = StringVar(value=current_fog or '')
    Radiobutton(edit_window, text='Fog realistic', variable=fog_var, value='fog_realistic').grid(row=4, column=2, sticky='w')
    Radiobutton(edit_window, text='Fog off', variable=fog_var, value='fog_off').grid(row=5, column=2, sticky='w')

    # Mission options
    Label(edit_window, text='Mission options').grid(row=3, column=3, sticky='w')
    mission_opt_var = StringVar(value=current_mission_options)
    Entry(edit_window, textvariable=mission_opt_var, width=30).grid(row=4, column=3, sticky='w')
    Label(edit_window, text='(enter without quotes)').grid(row=5, column=3, sticky='w')

    # Optional mission patch
    invincible_var = IntVar(value=0)
    Checkbutton(edit_window, text='Make Player 0 humans invincible', variable=invincible_var).grid(row=6, column=3, sticky='w')

    def remergeMain():
        _cleanup_workdir()
        root.deiconify()
        edit_window.destroy()

    def savechanges():
        new_status = _patch_status_mission(
            status_text,
            difficulty=diff_var.get() or None,
            fog_of_war=fog_var.get() or None,
            mission_options=mission_opt_var.get(),
        )
        _write_text(status_path, new_status)

        if invincible_var.get() == 1:
            mission_text = _read_text(mission_path)
            patched, changed = _make_player0_humans_invincible(mission_text)
            if changed > 0:
                _write_text(mission_path, patched)

        # If we came from a .sav, repack; if we came from folder open, do nothing.
        if save_path:
            try:
                _repack_save_preserving_entries(save_path, extracted_root)
                messagebox.showinfo('Saved', 'Mission/campaign save edited')
            except PermissionError:
                # Windows often denies overwriting if the game/Steam Cloud has the file open or it is read-only.
                suggested = os.path.splitext(os.path.basename(save_path))[0] + '_edited.sav'
                initialdir = os.path.dirname(save_path)
                dest = filedialog.asksaveasfilename(
                    title='Save edited .sav as',
                    initialdir=initialdir,
                    initialfile=suggested,
                    defaultextension='.sav',
                    filetypes=[('save files', '*.sav')],
                )
                if dest:
                    tmp_out = None
                    try:
                        fd, tmp_out = tempfile.mkstemp(prefix='goh_save_', suffix='.sav')
                        os.close(fd)
                        _repack_save_to_path(save_path, extracted_root, tmp_out)
                        os.replace(tmp_out, dest)
                        tmp_out = None
                        messagebox.showinfo('Saved', f'Could not overwrite original (file locked).\nSaved edited copy to:\n{dest}')
                    finally:
                        if tmp_out and os.path.exists(tmp_out):
                            try:
                                os.remove(tmp_out)
                            except OSError:
                                pass
                else:
                    messagebox.showwarning('Not saved', 'Could not overwrite the original save (locked) and no destination was selected.')
        else:
            messagebox.showinfo('Saved', 'Extracted mission folder edited')

        edit_window.destroy()
        root.destroy()

    Button(edit_window, text='Save changes', command=savechanges).grid(row=9, column=2)
    edit_window.protocol('WM_DELETE_WINDOW', remergeMain)


def _open_conquest_editor(save_path: str, extracted_root: str, status_path: str, campaign_path: str):
    # Original UI logic preserved, but driven by passed file paths.
    global mainSaveFile
    global secondarySaveFile
    mainSaveFile = status_path
    secondarySaveFile = campaign_path

    edit_window = Toplevel(root)
    edit_window.title('Editing the save')
    edit_window.minsize(700, 500)
    root.withdraw()
    global showMP
    global showSP
    global showAP
    global showRP
    showMP = StringVar()
    showSP = StringVar()
    showAP = StringVar()
    showRP = StringVar()
    resourceLabel = Label(edit_window, text='Resource editing')
    resourceLabel.grid(row=1, column=1)
    resourceLabelMP = Label(edit_window, text='Manpower')
    resourceLabelMP.grid(row=2, column=2)
    currentMP = Spinbox(edit_window, from_=1.00, to=99990.00, increment=100, textvariable=showMP)
    currentMP.grid(row=2, column=1)
    resourceLabelSP = Label(edit_window, text='Special points')
    resourceLabelSP.grid(row=3, column=2)
    currentSP = Spinbox(edit_window, from_=1.00, to=99990.00, increment=1, textvariable=showSP)
    currentSP.grid(row=3, column=1)
    resourceLabelAP = Label(edit_window, text='Ammo points')
    resourceLabelAP.grid(row=4, column=2)
    currentAP = Spinbox(edit_window, from_=1.00, to=99990.00, increment=100, textvariable=showAP)
    currentAP.grid(row=4, column=1)
    resourceLabelRP = Label(edit_window, text='Research points')
    resourceLabelRP.grid(row=5, column=2)
    currentRP = Spinbox(edit_window, from_=1.00, to=99990.00, increment=1, textvariable=showRP)
    currentRP.grid(row=5, column=1)
    global difficultyLevel
    difficultyLevel = IntVar()
    difficultyLevel.set(None)
    difficultyEasy = Radiobutton(edit_window, text='Easy', variable=difficultyLevel, value=1)
    difficultyNormal = Radiobutton(edit_window, text='Normal', variable=difficultyLevel, value=2)
    difficultyHard = Radiobutton(edit_window, text='Hard', variable=difficultyLevel, value=3)
    difficultyHeroic = Radiobutton(edit_window, text='Heroic', variable=difficultyLevel, value=4)
    difficultyLabel = Label(edit_window, text='Difficulty')
    difficultyLabel.grid(row=1, column=3)
    difficultyEasy.grid(row=2, column=3)
    difficultyNormal.grid(row=3, column=3)
    difficultyHard.grid(row=4, column=3)
    difficultyHeroic.grid(row=5, column=3)
    global fogOfWar
    fogOfWar = IntVar()
    fogOfWarOn = Radiobutton(edit_window, text='Fog of war on', variable=fogOfWar, value=1)
    fogOfWarOff = Radiobutton(edit_window, text='Fog of war off', variable=fogOfWar, value=2)
    fowLabel = Label(edit_window, text='Fog of war setting')
    fowLabel.grid(row=7, column=3)
    fogOfWarOn.grid(row=8, column=3)
    fogOfWarOff.grid(row=9, column=3)

    def remergeMain():
        _cleanup_workdir()
        root.deiconify()
        edit_window.destroy()

    def scanSave():
        newSave = _read_text(mainSaveFile)

        difficulty_match = re.search(r"\{difficulty\s+(easy|normal|hard|heroic)\}", newSave)
        fog_match = re.search(r"\{fogofwar\s+([^\}]+)\}", newSave)
        currentDifficultyText = difficulty_match.group(1) if difficulty_match else None
        fogOfWarText = fog_match.group(1).strip() if fog_match else None

        if "{difficulty easy}" in newSave:
            difficultyLevel.set(1)
        if '{difficulty normal}' in newSave:
            difficultyLevel.set(2)
        if "{difficulty hard}" in newSave:
            difficultyLevel.set(3)
        if "{difficulty heroic}" in newSave:
            difficultyLevel.set(4)
        if "{fogofwar fog_realistic}" in newSave:
            fogOfWar.set(1)
        if "{fogofwar fog_off}" in newSave:
            fogOfWar.set(2)

        mp_match = re.search(r"\{mp\s+(\d+)", newSave)
        rp_match = re.search(r"\{rp\s+(\d+)", newSave)
        ap_match = re.search(r"\{ap\s+(\d+)", newSave)
        sp_match = re.search(r"\{sp\s+(\d+)", newSave)

        mpInSave = mp_match.group(1) if mp_match else '0'
        rpInSave = rp_match.group(1) if rp_match else '0'
        apInSave = ap_match.group(1) if ap_match else '0'
        spInSave = sp_match.group(1) if sp_match else '0'

        showSP.set(spInSave)
        showAP.set(apInSave)
        showMP.set(mpInSave)
        showRP.set(rpInSave)

        modsActivated = re.findall(r'^\s*"mod_.+\s*$', newSave, flags=re.MULTILINE)

        allMods = modsActivated
        eachMod = StringVar(value=allMods)
        modsActiveLabel = Label(edit_window, text='Mods Active')
        modsActiveLabel.grid(row=18, column=1)
        modList = Listbox(edit_window, height=7, listvariable=eachMod)
        modList.grid(row=19, column=1)
        modList.config(width=40)

        def deleteMod():
            deleteThis = modList.curselection()
            modList.delete(deleteThis)

        def checkMod():
            checkThis = modList.curselection()
            checkThistoo = modList.get(checkThis)
            checkThistoo = checkThistoo.replace('\"mod_', '').strip()
            checkThistoo = checkThistoo.replace(':0"', '').strip()
            webbrowser.open_new('https://steamcommunity.com/sharedfiles/filedetails/?id=' + checkThistoo)

        checkWorkshopButton = Button(edit_window, text='Mod Steam Page', command=checkMod)
        checkWorkshopButton.grid(row=19, column=2)
        deleteModButton = Button(edit_window, text='Delete mod', command=deleteMod)
        deleteModButton.grid(row=19, column=3)

        edit_window.protocol("WM_DELETE_WINDOW", on_closing)

        def savechanges():
            newFile = newSave

            # Update resources (first occurrence only)
            newFile = re.sub(r"(\{mp\s+)\d+", r"\\g<1>" + str(int(float(showMP.get()))), newFile, count=1)
            newFile = re.sub(r"(\{sp\s+)\d+", r"\\g<1>" + str(int(float(showSP.get()))), newFile, count=1)
            newFile = re.sub(r"(\{rp\s+)\d+", r"\\g<1>" + str(int(float(showRP.get()))), newFile, count=1)
            newFile = re.sub(r"(\{ap\s+)\d+", r"\\g<1>" + str(int(float(showAP.get()))), newFile, count=1)

            # Update fog of war if present
            if fogOfWarText and fogOfWar.get() in (1, 2):
                desired_fog = 'fog_realistic' if fogOfWar.get() == 1 else 'fog_off'
                newFile = re.sub(r"(\{fogofwar\s+)[^\}]+(\})", r"\\g<1>" + desired_fog + r"\\g<2>", newFile, count=1)

            # Update difficulty if present
            if currentDifficultyText and difficultyLevel.get() in (1, 2, 3, 4):
                desired_diff = {1: 'easy', 2: 'normal', 3: 'hard', 4: 'heroic'}[difficultyLevel.get()]
                newFile = re.sub(r"(\{difficulty\s+)(easy|normal|hard|heroic)(\})", r"\\g<1>" + desired_diff + r"\\g<3>", newFile, count=1)

            # Apply mod list edits (including deletions)
            lines = newFile.splitlines(keepends=True)
            out_lines = []
            idx = 0
            for line in lines:
                if re.match(r'^\s*"mod_.+\s*$', line.strip()):
                    if idx < modList.size():
                        repl = modList.get(idx).rstrip('\r\n')
                        newline = '\r\n' if line.endswith('\r\n') else '\n'
                        out_lines.append(repl + newline)
                    idx += 1
                else:
                    out_lines.append(line)
            newFile = ''.join(out_lines)

            _write_text(mainSaveFile, newFile)
            if save_path:
                try:
                    _repack_save_preserving_entries(save_path, extracted_root)
                    messagebox.showinfo("Saved", "Save edited")
                except PermissionError:
                    suggested = os.path.splitext(os.path.basename(save_path))[0] + '_edited.sav'
                    initialdir = os.path.dirname(save_path)
                    dest = filedialog.asksaveasfilename(
                        title='Save edited .sav as',
                        initialdir=initialdir,
                        initialfile=suggested,
                        defaultextension='.sav',
                        filetypes=[('save files', '*.sav')],
                    )
                    if dest:
                        tmp_out = None
                        try:
                            fd, tmp_out = tempfile.mkstemp(prefix='goh_save_', suffix='.sav')
                            os.close(fd)
                            _repack_save_to_path(save_path, extracted_root, tmp_out)
                            os.replace(tmp_out, dest)
                            tmp_out = None
                            messagebox.showinfo('Saved', f'Could not overwrite original (file locked).\nSaved edited copy to:\n{dest}')
                        finally:
                            if tmp_out and os.path.exists(tmp_out):
                                try:
                                    os.remove(tmp_out)
                                except OSError:
                                    pass
                    else:
                        messagebox.showwarning('Not saved', 'Could not overwrite the original save (locked) and no destination was selected.')
            else:
                messagebox.showinfo("Saved", "Extracted conquest folder edited")

            edit_window.destroy()
            root.destroy()

        saveButton = Button(edit_window, text='Save changes', command=savechanges)
        saveButton.grid(row=20, column=2)

    try:
        scanSave()
    except Exception as e:
        messagebox.showerror('Error reading save', f'Failed to parse the save file.\n\n{e}')

    edit_window.protocol("WM_DELETE_WINDOW", remergeMain)


def _open_from_extracted_folder(folder_path: str):
    if not folder_path:
        return
    status_path = _find_file_by_basename(folder_path, 'status')
    if not status_path:
        messagebox.showerror('Unsupported folder', 'Could not find a status file in this folder.')
        return
    status_text = _read_text(status_path)
    save_kind = _detect_save_kind(status_text)
    if save_kind == 'conquest':
        campaign_path = _find_file_by_basename(folder_path, 'campaign.scn')
        if not campaign_path:
            messagebox.showerror('Unsupported folder', 'Conquest folder is missing campaign.scn')
            return
        _open_conquest_editor(save_path='', extracted_root=folder_path, status_path=status_path, campaign_path=campaign_path)
        return
    if save_kind == 'mission':
        mission_path = _find_file_by_basename(folder_path, 'mission.scn')
        if not mission_path:
            messagebox.showerror('Unsupported folder', 'Mission/campaign folder is missing mission.scn')
            return
        _open_mission_editor(save_path='', extracted_root=folder_path, status_path=status_path, mission_path=mission_path)
        return
    messagebox.showerror('Unsupported folder', 'Unrecognized save format in status file.')

def openfile():
    global _work_dir
    global mainSaveFile
    global secondarySaveFile

    save = filedialog.askopenfilename(initialdir=f"/", title="Select your save", filetypes=[('save files', "*.sav")])
    if not save:
        return

    _cleanup_workdir()
    _work_dir = tempfile.mkdtemp(prefix='goh_save_extract_')

    with zipfile.ZipFile(save, 'r') as s:
        s.extractall(_work_dir)

    status_path = _find_file_by_basename(_work_dir, 'status')
    if not status_path:
        messagebox.showerror('Unsupported save', 'Could not find status inside this .sav file.')
        _cleanup_workdir()
        return

    status_text = _read_text(status_path)
    save_kind = _detect_save_kind(status_text)

    if save_kind == 'conquest':
        campaign_path = _find_file_by_basename(_work_dir, 'campaign.scn')
        if not campaign_path:
            messagebox.showerror('Unsupported save', 'Could not find campaign.scn inside this .sav file.')
            _cleanup_workdir()
            return
        _open_conquest_editor(save_path=save, extracted_root=_work_dir, status_path=status_path, campaign_path=campaign_path)
        return

    if save_kind == 'mission':
        mission_path = _find_file_by_basename(_work_dir, 'mission.scn')
        if not mission_path:
            messagebox.showerror('Unsupported save', 'Could not find mission.scn inside this .sav file.')
            _cleanup_workdir()
            return
        _open_mission_editor(save_path=save, extracted_root=_work_dir, status_path=status_path, mission_path=mission_path)
        return

    messagebox.showerror('Unsupported save', 'Unrecognized save format: status does not look like conquest or mission save.')
    _cleanup_workdir()
    return

def infoText():
    messagebox.showinfo('help',
                        'MAKE A BACKUP of the save before using the programm! Disable Steam cloud (optional). Do not use it if you\'re afraid to break a save(shouldn\'t happen with a back up). Click on mod and "Mod steam page" to go to this mod workshop page.')

workButton = Button(root, text='Choose your save', command=openfile)
workButton.grid(row=2, column=1)

def openfolder():
    folder = filedialog.askdirectory(title='Select extracted save folder')
    if not folder:
        return
    _open_from_extracted_folder(folder)


folderButton = Button(root, text='Choose extracted folder', command=openfolder)
folderButton.grid(row=2, column=3)

workButton = Button(root, text='Read this info', command=infoText)
workButton.grid(row=2, column=2)

def on_closing():
    try:
        _cleanup_workdir()
        root.destroy()
    except FileNotFoundError:
        root.destroy()

root.mainloop()