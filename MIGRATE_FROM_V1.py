# coding: utf-8
"""Move (not copy) cached V1 data into the sibling V2 directory without overwriting.
Do not run while V1 or V2 is running. Python 3.8+.
"""
import sys
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / 'ParallelLaughPipeline'
SENTINEL = 'DA DUNG'


def main():
    print('=== CHUYEN DU LIEU TU V1 SANG V2 (KHONG COPY VIDEO) ===')
    print('V1:', OLD)
    print('V2:', HERE)
    if not (OLD / 'pipeline_common.py').is_file():
        print('KHONG TIM THAY V1. Dung, khong sua gi.')
        return 2
    if OLD.resolve() == HERE.resolve():
        print('LOI: V1 va V2 trung thu muc.'); return 2
    print('\nBAT BUOC TAT START_PIPELINE.bat, START_STUDIO.bat va Python worker dang chay truoc khi chuyen.')
    print('Script chi MOVE cac thu muc job va file video/ket qua chua trung; khong xoa code V1.')
    print('Job trung ten tai V2 se duoc BO QUA, khong ghi de.')
    if input('Go [' + SENTINEL + '] de xac nhan: ').strip() != SENTINEL:
        print('Huy. Khong doi gi.'); return 0
    moved=0;skipped=0
    for folder in ('jobs','input_videos','done'):
        source=OLD/folder
        target=HERE/folder
        if not source.is_dir():
            continue
        target.mkdir(exist_ok=True,parents=True)
        for item in sorted(source.iterdir()):
            dest=target/item.name
            if dest.exists():
                print('BO QUA (da ton tai):',folder,item.name)
                skipped+=1
                continue
            # Skip temp/in-progress directories and partial downloads.
            if item.name.startswith(('.downloading_', '.importing_')):
                print('BO QUA (dang do):',folder,item.name)
                skipped+=1
                continue
            if folder=='jobs' and (not item.is_dir() or not (item/'job.json').is_file()):
                print('BO QUA (job khong hop le):',item.name)
                skipped+=1
                continue
            try:
                item.rename(dest) # Same drive => move/rename, no video copy
                print('DA CHUYEN:',folder,item.name)
                moved+=1
            except OSError as exc:
                print('LOI CHUYEN:',item,'->',dest,exc)
                skipped+=1
    for name in ('list.txt','doneLink.txt'):
        source=OLD/name
        target=HERE/name
        if not source.is_file(): continue
        src=source.read_text(encoding='utf-8-sig').splitlines()
        dst=target.read_text(encoding='utf-8-sig').splitlines() if target.exists() else []
        seen={x.strip() for x in dst if x.strip() and not x.lstrip().startswith('#')}
        added=0
        for line in src:
            v=line.strip()
            if not v or v.startswith('#') or v in seen: continue
            dst.append(line)
            seen.add(v)
            added+=1
        if added:
            target.write_text('\n'.join(dst).rstrip()+'\n',encoding='utf-8')
        print('DANH SACH:',name,'+%d dong moi (V1 van giu file goc)'%added)
    print('\nXONG: da chuyen %d muc, bo qua %d muc. KHONG tu xoa V1.'%(moved,skipped))
    print('Mo START_STUDIO.bat trong V2. Chi xoa V1 khi da kiem tra video, CSV va clip dau ra.')
    if skipped:
        print('Chu y: con muc bo qua tai V1. Kiem tra thu cong truoc khi don dep.')
    return 0


if __name__=='__main__':
    try:
        code=main()
    except (EOFError,KeyboardInterrupt):
        print('Huy bo')
        code=1
    sys.exit(code)
