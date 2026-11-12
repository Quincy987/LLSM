"""Find named records in a saved .esa and show where known doubles and GUIDs sit after the name."""
import struct, sys, uuid
sys.path.insert(0, "src")
from pathlib import Path
from llsm.scia.esa_file import iter_blocks, names_in

path = Path(sys.argv[1]); want = sys.argv[2]; values = [float(v) for v in sys.argv[3].split(",")] if len(sys.argv) > 3 else []
span = int(sys.argv[4]) if len(sys.argv) > 4 else 400
GUIDS = {
    "case": "0908D21F-481F-11D4-AB84-00C06C452330", "group": "F9D4AA72-49D5-11D4-A3CF-000000000000",
    "freeF": "E03984FC-B420-4C03-8D2F-72EA2FAB147D", "beamF": "BC16B3C2-F464-11D4-94D3-000000000000",
    "beam": "ECB5D684-7357-11D4-9F6C-00104BC3B443", "node": "39A7F468-A0D4-4DFF-8E5C-5843E1807D13",
    "plane": "8708ED31-8E66-11D4-AD94-F6F5DE2BE344", "combi": "C0FBF7E1-4A71-11D4-AB86-00C06C452330",
}
for bi, chunk in enumerate(iter_blocks(path.read_bytes())):
    for start, name in names_in(chunk):
        if name != want:
            continue
        end = start + 2 + 2 * len(name)
        print(f"block {bi} len {len(chunk)} name@{start} ends@{end}")
        win = chunk[end:end + span]
        for g, s in GUIDS.items():
            b = uuid.UUID(s).bytes_le; i = win.find(b)
            while i >= 0:
                print(f"   guid {g} at +{i}"); i = win.find(b, i + 1)
        for v in values:
            b = struct.pack("<d", v); i = win.find(b)
            while i >= 0:
                print(f"   {v} at +{i}"); i = win.find(b, i + 1)
        for off, nm in names_in(win):
            print(f"   str {nm!r} at +{off}")
        print("   ints:", [(o, struct.unpack_from('<I', win, o)[0]) for o in range(0, min(120, len(win) - 4), 4)])
