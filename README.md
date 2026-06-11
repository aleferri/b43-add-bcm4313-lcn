# b43 LCN-PHY port — BCM4313 (A4100N)

Far funzionare il Wi-Fi dell'A4100N (Broadcom BCM4313, LCN-PHY) sotto il
driver aperto `b43`, completando il supporto LCN-PHY di b43.

## Perché questa strada

- Il driver SoftMAC mainline `brcmsmac` distribuisce un ucode aperto pensato
  per le varianti desktop/dongle del BCM4313. Sul 4313 embedded dell'A4100N
  l'ucode si carica ma il PSM del MAC non completa mai l'auto-init:
  `ucode did not self-suspend`.
- Quel silicio vuole l'ucode **proprietario**. Quell'ucode non è
  ridistribuibile da solo: lo si estrae dal driver proprietario
  (ridistribuibile, ma solo in forma binaria) al momento della build con
  `b43-fwcutter`. È il modello di b43.
- `brcmsmac` parla solo l'ABI dell'ucode aperto, quindi non può pilotare
  questa parte. Da cui: b43 + ucode proprietario + un LCN-PHY funzionante.
- Il LCN-PHY di b43 esiste (`CONFIG_B43_PHY_LCN`) ma è marcato BROKEN e la
  logica PHY è una transliterazione a mano da `brcmsmac` rimasta incompleta.

## Licenza

Il codice PHY di `brcmsmac` è sotto licenza permissiva tipo ISC/BSD,
compatibile con la GPL-2.0: transliterarne la logica PHY dentro b43
(GPL-2.0) è lecito. Solo l'*ucode* è proprietario — gestito da fwcutter,
fuori dal tree.

## Cosa resta davvero

La catena di init in `b43/phy_lcn.c` è già transliterata
(`op_init` → AFE → tabelle → baseband → radio 2064 → tx-power → channel),
e ogni funzione ha in cima il commento con la sua sorgente brcmsmac. Il
lavoro è chiudere i buchi `TODO`/`FIXME` e farlo smettere di crashare — non
una riscrittura.

Vedi `docs/transliteration-manual.md` per la mappatura delle primitive.

Il riconoscimento del PHY LCN, l'allocazione delle ops e la scelta
dell'ucode (`ucode24_lcn`/`ucode25_lcn` per core_rev 24/25/28) sono **già
in mainline**, ma compilati fuori finché `CONFIG_B43_PHY_LCN` dipende da
`BROKEN` — vedi patch 0003.

## Piano

### Fase 1 — bring-up legacy (b/g)
Obiettivo: il PHY si inizializza, l'ucode proprietario raggiunge il
self-suspend, si associa e passa traffico a rate legacy. HT/AMPDU NON
cablati. tx-power cal neutralizzata (indice fisso).

### Fase 2 — HT (target 150 Mbit, 1x1 HT40 SGI MCS7)
Controllo di tx-power reale + calibrazione, poi capability HT + path di
aggregazione A-MPDU (b43 non ha un framework HT da riusare — è il grosso
della fase 2).

## TODO

Fase 1:
- [x] R-cal: aggiungere il poll del done-bit mancante in `b43_radio_2064_init` → patch 0001
- [x] `tx_pwr_ctl_init` per il legacy: open-loop a guadagno fisso; sensing temp/vbat mantenuto, con ADC pwrup calcolato come il wl (`rfseq_tbl_adc_pwrup`, floor 1600) invece dell'hardcoded 0x640 → patch 0002
- [x] Rendere `CONFIG_B43_PHY_LCN` selezionabile, così b43 riconosce il 4313 e prova a caricare l'ucode → patch 0003
- [ ] Rivedere i TODO di bring-up rimasti (il ramo 5G in radio init è fuori scope: il 4313 è solo 2.4 GHz)
- [ ] Build con `CONFIG_B43_PHY_LCN`, verificare nessun crash al probe e il self-suspend dell'ucode
- [x] Cal RX (in b43 non c'era alcuna routine di calibrazione): porta `wlc_lcnphy_rx_iq_cal`, gate per ricevere i beacon → patch 0004. NB: `rc_cal` è già inline in `b43_radio_2064_init` (0x933–0x937 = default LCNREV 1), non va portato
- [x] Cache RX-IQ per-canale + invalidazione su drift di temperatura (watchdog) → patch 0005
- [ ] Confermare associazione + iperf a b/g

Fase 2:
- [ ] Controllo/calibrazione tx-power reale (il cluster "brcmsmac is outdated here")
- [ ] Advertisement delle capability HT
- [ ] Path di aggregazione A-MPDU

## Patch

| #    | Stato            | Sommario |
|------|------------------|----------|
| 0001 | bozza, non testata | b43: lcn: attendere il completamento dell'R-cal in radio 2064 init |
| 0002 | bozza, non testata | b43: lcn: tx-power open-loop a guadagno fisso per il bring-up legacy |
| 0003 | bozza, non testata | b43: lcn: rendere CONFIG_B43_PHY_LCN selezionabile (sperimentale) |

Tutte le patch sono transliterazioni contro b43 **mainline** e sono **non
testate su hardware**. Rebasare sul tuo tree di destinazione (la versione
del kernel OpenWrt differisce — i nomi di struct/framework cambiano) prima
di buildare.

## Verifica contro il blob

`wl.ko` (driver proprietario, MIPS32 BE, non strippato) e' la verita' di
riferimento per questo silicio. `tools/verify_blob.py` disassembla le funzioni
`wlc_lcnphy_*` e confronta le costanti delle patch con quelle reali:

    python3 -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt
    python3 tools/verify_blob.py

Stato e risultati in `docs/blob-verification.md`: 0002 confermata dal blob,
0001 divergenza benigna (poll presente in brcmsmac, budget allineato a 10 s),
0004 preflight rc_cal (landmark 0x933..0x937 confermato).

## Build / test

1. Estrarre l'ucode proprietario: `b43-fwcutter` sul driver proprietario del BCM4313.
2. Kernel: abilitare `CONFIG_B43` + `CONFIG_B43_PHY_LCN` (oggi `depends on BROKEN`).
3. Applicare le patch in `patches/` (`git am` oppure `git apply`).
4. Probe, e seguire in dmesg il percorso di init e il self-suspend.
