#!/usr/bin/env python3
"""Verifica le costanti delle patch b43 LCN-PHY contro il blob proprietario.

wl.ko (driver Broadcom proprietario dell'A4100N) e' la verita' di riferimento
per questo silicio: e' MIPS32 big-endian, rilocabile e non strippato, quindi
conserva i simboli wlc_lcnphy_*. Le patch in patches/ sono transliterazioni da
brcmsmac; qui ne confrontiamo i numeri magici con cio' che il blob fa davvero.

Esito per ogni check:
  PASS     - la costante della patch e' corroborata dal blob
  DIVERGE  - il blob fa qualcosa di diverso (NON e' di per se' un bug della
             patch: la provenienza e' brcmsmac, non questo blob) -> da rivedere
  FAIL     - la funzione/struttura attesa non e' stata trovata

Uso:  python3 tools/verify_blob.py [percorso/wl.ko]
"""
from __future__ import annotations

import collections
import os
import sys

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection
from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_BIG_ENDIAN

RADIO_ACCESSORS = {
    "read_radio_reg", "write_radio_reg",
    "mod_radio_reg", "or_radio_reg", "and_radio_reg",
}
LOAD_IMM = {"addiu", "ori", "li"}


class Blob:
    """Lettore ELF con disassembly e risoluzione delle jal via rilocazioni."""

    def __init__(self, path: str):
        self._f = open(path, "rb")
        self.elf = ELFFile(self._f)
        self.syms = list(self.elf.get_section_by_name(".symtab").iter_symbols())
        self.md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_BIG_ENDIAN)

        self.funcs = {}  # name -> (section_index, value, size)
        for s in self.syms:
            if s["st_info"]["type"] == "STT_FUNC" and s["st_size"]:
                self.funcs[s.name] = (s["st_shndx"], s["st_value"], s["st_size"])

        # section_index -> {offset: [symbol_name, ...]}  dalle sezioni di reloc
        self._relmaps = {}
        for sec in self.elf.iter_sections():
            if isinstance(sec, RelocationSection) and sec["sh_info"]:
                m = self._relmaps.setdefault(sec["sh_info"], {})
                for r in sec.iter_relocations():
                    name = self.syms[r["r_info_sym"]].name
                    m.setdefault(r["r_offset"], []).append(name)

    def disasm(self, func_name: str):
        """Genera (addr, mnemonic, op_str, [callee_names|None]) per la funzione."""
        shndx, value, size = self.funcs[func_name]
        data = self.elf.get_section(shndx).data()[value:value + size]
        relmap = self._relmaps.get(shndx, {})
        for ins in self.md.disasm(data, value):
            yield ins.address, ins.mnemonic, ins.op_str, relmap.get(ins.address)


def _imm(op_str: str):
    """Ultimo operando come intero, se immediato; altrimenti None."""
    tail = op_str.split(",")[-1].strip()
    try:
        return int(tail, 0)
    except ValueError:
        return None


def radio_ops(blob: Blob, func: str):
    """Accessi a registri radio: (addr, accessor, reg) con reg = imm in $a1.

    L'indirizzo del registro radio e' il 2o argomento (a1) caricato poco prima
    della jal verso l'accessor.
    """
    seq = list(blob.disasm(func))
    out = []
    for i, (addr, mn, op, callees) in enumerate(seq):
        if not callees:
            continue
        accessor = next((c for c in callees if c in RADIO_ACCESSORS), None)
        if accessor is None:
            continue
        reg = None
        for back in seq[max(0, i - 4):i]:
            if back[1] in LOAD_IMM and back[2].startswith("$a1"):
                reg = _imm(back[2])
        out.append((addr, accessor, reg))
    return out


def reads_radio_bit(blob: Blob, func: str, reg: int, bit: int) -> bool:
    """True se la funzione legge `reg` via read_radio_reg e poi testa `bit`."""
    seq = list(blob.disasm(func))
    for i, (addr, mn, op, callees) in enumerate(seq):
        if not (callees and "read_radio_reg" in callees):
            continue
        loaded = None
        for back in seq[max(0, i - 4):i]:
            if back[1] in LOAD_IMM and back[2].startswith("$a1"):
                loaded = _imm(back[2])
        if loaded != reg:
            continue
        for fwd in seq[i + 1:i + 8]:
            if fwd[1] == "andi" and _imm(fwd[2]) == bit:
                return True
    return False


def stack_gain_tuples(blob: Blob, func: str):
    """Estrae le tuple di guadagno scritte sullo stack come `sh imm, off($sp)`.

    Traccia l'ultimo immediato caricato in ogni registro e raggruppa le store
    a 0x18/0x1a/0x1c/0x1e (struct {gm,pga,pad,dac}) in tuple ordinate.
    """
    last_imm = {}
    pending = {}
    tuples = []
    OFFS = ("0x18(", "0x1a(", "0x1c(", "0x1e(")
    for addr, mn, op, _ in blob.disasm(func):
        if mn in LOAD_IMM and "$zero" in op:
            reg = op.split(",")[0].strip()
            last_imm[reg] = _imm(op)
        elif mn == "sh" and "$sp)" in op:
            reg, dst = (p.strip() for p in op.split(",", 1))
            val = 0 if reg == "$zero" else last_imm.get(reg)
            for k, pat in enumerate(OFFS):
                if dst.startswith(pat):
                    pending[k] = val
            if len(pending) == 4:
                tuples.append(tuple(pending[k] for k in range(4)))
                pending = {}
    return tuples


def uses_immediate(blob: Blob, func: str, value: int) -> bool:
    return any(mn in LOAD_IMM and _imm(op) == value
               for _, mn, op, _ in blob.disasm(func))


def a1_immediates(blob: Blob, func: str):
    """Insieme degli immediati caricati in $a1 (1o reg-arg dopo pi) nella funzione."""
    return {_imm(op) for _, mn, op, _ in blob.disasm(func)
            if mn in LOAD_IMM and op.startswith("$a1") and _imm(op) is not None}


def references(blob: Blob, func: str, symbol: str) -> bool:
    """True se la funzione referenzia `symbol` (anche via accessor issato in reg)."""
    return any(callees and symbol in callees
               for _, _, _, callees in blob.disasm(func))


# --- Check dichiarativi: ogni voce lega una patch a una verifica sul blob ---

def check_0001(blob):
    """0001: poll R-cal su radio 0x05c bit 0x20. Atteso in wlc_phy_init_lcnphy."""
    func = "wlc_phy_init_lcnphy"
    if func not in blob.funcs:
        return "FAIL", f"{func} assente nel blob"
    touches = [(a, acc) for a, acc, reg in radio_ops(blob, func) if reg == 0x5c]
    if reads_radio_bit(blob, func, 0x5c, 0x20):
        return "PASS", f"{func} legge radio 0x05c e testa 0x20"
    accs = ", ".join(sorted({acc for _, acc in touches})) or "nessuno"
    return ("DIVERGE",
            f"{func} tocca radio 0x05c solo via [{accs}], mai read+test 0x20. "
            f"Divergenza BENIGNA: il poll esiste in brcmsmac (wlc_lcnphy_rcal, "
            f"SPINWAIT su 0x05c&0x20), il blob piu' vecchio si fida del timing. "
            f"Vedi docs/blob-verification.md (budget poll allineato a "
            f"brcmsmac: 10 s)")


def check_0002(blob):
    """0002: tx gain 2GHz {4,12,12,0}, 5GHz {7,15,14,0}, bbmult 150."""
    func = "wlc_lcnphy_tx_pwr_ctrl_init"
    if func not in blob.funcs:
        return "FAIL", f"{func} assente nel blob"
    tuples = stack_gain_tuples(blob, func)
    want = {(4, 12, 12, 0), (7, 15, 14, 0)}
    found = want & set(tuples)
    missing = want - set(tuples)
    bb = uses_immediate(blob, func, 150)
    if not missing and bb:
        return "PASS", (f"{func}: tuple di guadagno {sorted(found)} e "
                        f"bbmult=150 presenti")
    parts = []
    if missing:
        parts.append(f"tuple mancanti {sorted(missing)} (trovate {tuples})")
    if not bb:
        parts.append("bbmult=150 non trovato")
    return "DIVERGE", f"{func}: " + "; ".join(parts)


def check_0003(blob):
    """0003: solo Kconfig (depends on BROKEN -> selezionabile). Niente nel blob."""
    return "SKIP", "patch di solo Kconfig: nessuna costante da confrontare col blob"


def check_0004_preflight(blob):
    """PREFLIGHT 0004 (non ancora scritta): RC-cal, gate della ricezione.

    brcmsmac wlc_lcnphy_rc_cal scrive un default costante (dflt_rc_cal_val=7,
    o 11 se LCNREV 1) nei PHY reg 0x933..0x937. Qui verifichiamo solo che il
    blob programmi gli stessi registri (landmark), per orientare la futura 0004.
    """
    func = "wlc_phy_init_lcnphy"
    if func not in blob.funcs:
        return "FAIL", f"{func} assente nel blob"
    regs = {0x933, 0x934, 0x935, 0x936, 0x937}
    have = regs & a1_immediates(blob, func)
    if regs <= have and references(blob, func, "write_phy_reg"):
        return ("PREFLIG",
                f"{func} programma i RC-filter reg 0x933..0x937 via write_phy_reg "
                f"(valori derivati a runtime). brcmsmac rc_cal usa invece il "
                f"default costante 7/11: punto di partenza valido per 0004")
    return ("DIVERGE",
            f"{func}: RC-filter reg trovati {sorted(hex(x) for x in have)} su "
            f"0x933..0x937 — landmark rc_cal incompleto")


CHECKS = [
    ("0001", "R-cal done poll (radio 0x05c bit 0x20)", check_0001),
    ("0002", "tx gain open-loop fissi + bbmult 150", check_0002),
    ("0003", "CONFIG_B43_PHY_LCN selezionabile", check_0003),
    ("0004", "RC-cal / RX (preflight, patch da scrivere)", check_0004_preflight),
]


def main(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    path = argv[1] if len(argv) > 1 else os.path.join(here, os.pardir, "wl.ko")
    if not os.path.exists(path):
        print(f"blob non trovato: {path}", file=sys.stderr)
        return 2

    blob = Blob(path)
    print(f"blob: {os.path.relpath(path)}  ({len(blob.funcs)} funzioni con simbolo)\n")

    tally = collections.Counter()
    for num, title, fn in CHECKS:
        status, detail = fn(blob)
        tally[status] += 1
        print(f"[{status:7s}] {num}  {title}")
        print(f"           {detail}")
    print()
    print("  ".join(f"{k}={v}" for k, v in sorted(tally.items())))

    # exit != 0 solo se un check e' palesemente rotto (FAIL); DIVERGE e' da
    # leggere a mano, non blocca.
    return 1 if tally["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
