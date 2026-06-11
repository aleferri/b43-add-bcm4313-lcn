# Manuale di transliterazione brcmsmac → b43 (LCN-PHY)

Portare la logica LCN-PHY da `brcmsmac`
(`.../brcm80211/brcmsmac/phy/phy_lcn.c`, `phytbl_lcn.c`) dentro `b43`
(`.../b43/phy_lcn.c`, `tables_phy_lcn.c`) è una transliterazione meccanica.
Le primitive mappano 1:1 tranne la convenzione della maschera.

## Accessori ai registri

| brcmsmac | b43 |
|---|---|
| `read_phy_reg(pi, A)` | `b43_phy_read(dev, A)` |
| `write_phy_reg(pi, A, V)` | `b43_phy_write(dev, A, V)` |
| `mod_phy_reg(pi, A, M, V)` | `b43_phy_maskset(dev, A, ~M, (V & M))` |
| `or_phy_reg(pi, A, V)` | `b43_phy_set(dev, A, V)` |
| `and_phy_reg(pi, A, M)` | `b43_phy_mask(dev, A, M)` |
| `read_radio_reg(pi, A)` | `b43_radio_read(dev, A)` |
| `write_radio_reg(pi, A, V)` | `b43_radio_write(dev, A, V)` |
| `or_radio_reg(pi, A, V)` | `b43_radio_set(dev, A, V)` |
| `and_radio_reg(pi, A, M)` | `b43_radio_mask(dev, A, M)` |

### Il trabocchetto della maschera

`mod_phy_reg(pi, A, M, V)` fa `(reg & ~M) | (V & M)` — `M` seleziona i bit
da **cambiare**. `b43_phy_maskset(dev, A, mask, set)` fa
`(reg & mask) | set` — `mask` seleziona i bit da **tenere**. Quindi la
maschera è invertita:

    mod_phy_reg(pi, A, M, V)   →   b43_phy_maskset(dev, A, ~M, V & M)

Verificato sulle implementazioni: `mod_phy_reg` in
`brcmsmac/phy/phy_cmn.c` (`val &= mask; maskset16(~mask, val)`),
`b43_phy_maskset` in `b43/phy_common.c`.

## Tabelle

I caricamenti tabella di `brcmsmac` (`wlc_lcnphy_write_table` /
`struct phytbl_info`) → in b43 `b43_lcntab_write` con gli indirizzi
`B43_LCNTAB*()`. I **valori** dei dati si portano direttamente; cambia solo
il wrapper di accesso.

## Controllo di flusso / timing

- `SPINWAIT(cond, us)` → loop di poll limitato con `udelay()` (b43 non ha
  SPINWAIT). Mantenere lo stesso budget di timeout.
- `mdelay()` / `udelay()` si portano tali e quali.

## Stato

`pi->...` (`struct brcms_phy`) → `dev->phy.lcn->...`
(`struct b43_phy_lcn`) o `dev`. Lo struct di b43 è minimale; aggiungere i
campi man mano che le funzioni portate li richiedono.

## Dove il dizionario finisce (NON 1:1)

Tutto ciò che tocca la SHM / l'interfaccia ucode
(`wlapi_bmac_*_shm`, offset `M_UCODE_*`, cal di tx-power assistita
dall'ucode). b43 gira l'ucode **proprietario** con una mappa SHM diversa da
quella che il codice `brcmsmac` presume. Questi punti vogliono gli offset
SHM propri di b43, non una transliterazione cieca — e sono esattamente le
parti che la Fase 1 rinvia.
