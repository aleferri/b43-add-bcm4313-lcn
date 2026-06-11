# Verifica delle patch contro il blob proprietario (wl.ko)

Le patch in `patches/` sono transliterazioni da `brcmsmac`. Questo documento
le confronta con `wl.ko`, il driver Broadcom proprietario dell'A4100N, che per
questo silicio e' la verita' di riferimento: e' un modulo MIPS32 big-endian,
rilocabile e **non strippato**, quindi conserva i simboli `wlc_lcnphy_*` e si
puo' disassemblare funzione per funzione.

## Metodo

`tools/verify_blob.py` carica l'ELF con pyelftools, disassembla con capstone
(`MIPS32 | BIG_ENDIAN`) e risolve i bersagli delle `jal` tramite le sezioni di
rilocazione (negli oggetti rilocabili i target di chiamata sono relocation
`R_MIPS_26`, non immediati). Gli accessi ai registri radio passano per funzioni
reali e non inline (`read_radio_reg`, `write_radio_reg`, `mod/or/and_radio_reg`),
quindi l'indirizzo del registro e' l'immediato caricato in `$a1` (2o argomento
o32) appena prima della chiamata. Su questo si reggono i due estrattori:
accessi radio e tuple di guadagno scritte sullo stack.

Esiti: `PASS` (corroborato dal blob), `DIVERGE` (il blob fa altro; da leggere a
mano, non implica di per se' un bug della patch perche' la provenienza e'
brcmsmac), `FAIL` (struttura attesa assente), `SKIP` (niente da confrontare).

## Risultati iniziali

### 0002 — tx gain open-loop + bbmult 150 → PASS

In `wlc_lcnphy_tx_pwr_ctrl_init` il blob scrive sullo stack la struct
`{gm, pga, pad, dac}` con due rami:

- 2.4 GHz: `{4, 12, 12, 0}`
- 5 GHz:   `{7, 15, 14, 0}`

e usa `bbmult = 150 (0x96)`. Sono esattamente le costanti della patch 0002. Le
costanti del bring-up legacy sono quindi confermate dal silicio reale.

### 0001 — poll R-cal su radio 0x05c bit 0x20 → DIVERGE benigna

Nel blob il registro radio `0x05c` compare **solo** dentro
`wlc_phy_init_lcnphy` e sempre via `and_radio_reg` (mascheramento); non viene
**mai** letto con `read_radio_reg` e testato contro il bit `0x20`. Il blob fa
la stessa sequenza di contorno della patch (set `0x5b`, poi mask `0x5c`/`0x5b`/
`0x57`) ma si fida del timing fisso e non attende alcun done-bit.

Incrociando col sorgente brcmsmac (`wlc_lcnphy_rcal`, da cui la patch e'
transliterata) la divergenza si scioglie ed e' **benigna**:

    or_radio_reg(REG057, 0x01);
    or_radio_reg(REG05B, 0x02);
    mdelay(5);
    SPINWAIT(!wlc_radio_2064_rcal_done(pi), 10 * 1000 * 1000);  /* 10 s */
    ...
    and_radio_reg(REG05B, 0xfD);
    and_radio_reg(REG057, 0xFE);

con `wlc_radio_2064_rcal_done` = `read_radio_reg(REG05C) & 0x20`. Quindi:

- Il poll **esiste davvero** in brcmsmac: la patch e' una transliterazione
  fedele, non un numero inventato.
- Il done-bit `0x20` e' un segnale **hardware della radio** (fine calibrazione
  del resistore), non un handshake dell'ucode.
- brcmsmac legge `rcal_value & 0x1f` dopo il poll ma poi lo **scarta**: il
  SPINWAIT serve solo come attesa di settling. Ometterne la lettura in Fase 1,
  come fa la patch, e' corretto.
- Il driver proprietario (piu' vecchio) semplicemente non fa questo poll e si
  affida al timing. Sono due strategie valide; quella di brcmsmac e' la piu'
  difensiva (attende il done-bit anziche' fidarsi di un ritardo fisso). Nessuna
  delle due e' "sbagliata".

Conclusione: l'output DIVERGE del verificatore e' corretto (il blob davvero non
fa il poll), ma non e' un bug della patch — e' una scelta presente solo nel
driver aperto.

#### Budget di timeout — allineato a brcmsmac

In origine la patch implementava il poll come `10000 * udelay(10)` = 100 ms,
contro il tetto di `10 * 1000 * 1000 µs` = 10 s di brcmsmac (~100x piu' corto),
in violazione della regola "mantenere lo stesso budget" del manuale. **Corretto**:
il loop e' ora `1000000 * udelay(10)` = 10 s, identico al SPINWAIT di brcmsmac.
Il poll esce comunque appena il done-bit e' alto, quindi nel caso normale
l'attesa reale resta nell'ordine dei microsecondi; i 10 s sono solo il soffitto.

#### Ritrattazione

Nel giro precedente avevo ipotizzato che l'R-cal fosse gestito a livello PMU via
`si_pmu_rcal`. **Falso**: `si_pmu_rcal` e' chiamato solo da
`wlc_sslpnphy_radio_init` (SSLPN-PHY) e non tocca il path LCN.

### 0003 — Kconfig selezionabile → SKIP

Modifica di solo `Kconfig`: nessuna costante hardware da confrontare col blob.

### 0004 — RC-cal / RX (PREFLIGHT, patch ancora da scrivere)

`rc_cal` e' il gate per ricevere i beacon (vedi README). La patch 0004 non
esiste ancora; questo e' lavoro di pre-validazione per orientarla.

Riscontri:

- Il blob programma i registri di filtro RC `0x933..0x937` dentro
  `wlc_phy_init_lcnphy` via `write_phy_reg` (l'accessor e' issato in `$s3`,
  risolto a `write_phy_reg`), con valori **derivati a runtime** (da stato in
  `$s6/$s7`/stack, verosimilmente da NVRAM/cal).
- brcmsmac `wlc_lcnphy_rc_cal` e' la variante **a default costante**: scrive
  `flt_val = (v<<10)|(v<<5)|v` su 0x933..0x936 e `flt_val & 0x1ff` su 0x937,
  con `v = dflt_rc_cal_val` = 7 (oppure 11 se `LCNREV_IS(phy_rev, 1)`). Non
  misura nulla: programma coefficienti di filtro di default.

Indicazione per 0004: in Fase 1 portare la variante a default di brcmsmac e' un
punto di partenza valido (sblocca la RX senza calibrazione reale); i valori
runtime del blob sono semmai un raffinamento di Fase 2. Il check
`check_0004_preflight` in `verify_blob.py` verifica solo il landmark (i cinque
registri programmati), non i valori.

## Prossimi passi

- 0001: budget del poll allineato a brcmsmac (10 s). Divergenza col blob
  chiarita: benigna.
- 0004: scrivere la patch rc_cal portando la variante a default di brcmsmac
  (`dflt_rc_cal_val` 7/11 sui PHY reg 0x933..0x937), poi aggiungere il check sui
  valori (non solo sul landmark).
