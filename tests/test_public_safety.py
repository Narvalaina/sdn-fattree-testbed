from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TEXT_SUFFIXES={".py",".md",".toml",".json",".csv",".txt",".example"}

def test_no_machine_specific_paths_or_hardcoded_password():
    forbidden=["/" + "home" + "/", "C:" + "\\" + "Users", "One" + "Drive", "admin" + ":" + "admin"]
    offenders=[]
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        txt=p.read_text(encoding="utf-8",errors="ignore")
        for needle in forbidden:
            if needle in txt:
                offenders.append((str(p.relative_to(ROOT)),needle))
    assert not offenders, offenders
