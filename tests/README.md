# Testy

```bash
python tests/run_all.py            # vsetky
python tests/run_all.py triage tp  # len tie, ktorych meno obsahuje "triage" alebo "tp"
python tests/test_triage.py        # jeden samostatne (plny vypis)
```

Ziadny pytest - kazdy test je samostatny skript, ktory si **sam nastavi
`DATABASE_URL` na sqlite v TEMP pred importom `db.py`**. Bezia vo vlastnych
procesoch, takze sa navzajom neovplyvnuju a nikdy sa nedotknu produkcnej
Postgres DB. Uspech = navratovy kod 0 (a hlaska `VSETKY TESTY PRESLI`).

Nespustaj ich s nastavenym `DATABASE_PUBLIC_URL` v prostredi - `test_prompt`
by potom cital produkcnu DB.

## Co ktory test strazi

| test | strazi |
|---|---|
| `test_prompt` | system prompt sa zostavi pre VSETKY assety (brace bug z 30.7. - `{}` v texte rozbije `str.format`) |
| `test_prompt_no_threshold` | v ziadnom prompte ani scheme nie je prah na otvorenie (ani cislo, ani veta, z ktorej sa da odvodit) |
| `test_watch_break_gate` | brana na plytke prerazenie watch urovne (0.30 ATR) + auto-watch po zamietnuti |
| `test_tp_claude` | TP je Claudov, pasmo 0.5-5x kalibracie, podlaha pomeru TP/SL 1.0 |
| `test_conf_sizing` | marza sa skaluje confidence, Claude vzorec nepozna |
| `test_triage` | dvojfazovy cyklus: prompt skenu, gating (kedy sa nepyta), rezimy off/shadow/active |
| `test_perf_facts` | spocitane fakty o vykonnosti: prahy riadkov (n>=20/10), kalibracia, cache |
| `test_retro_loop` | den sa oznaci za spracovany aj bez reflexie (MINIMAX slucka 3.9.) + minutovy health poller je prec |
| `test_escalation` | eskalacia health checku: makro trigger + cooldown per druh triggeru |
| `test_dispatch` | planovane cykly bezia na pozadi, tik nezablokuje nasledujuce |
| `test_pre_macro` | watcher nastaveny VOPRED pred makro udalostou namiesto hromadneho cyklu |
| `test_regime_boundary` | zhluk na prechode obchodnych hodin (slotova mriezka) |
| `test_schedule_line` | cas najblizsieho planovaneho behu v prompte |
| `test_volume_fix` | objem prebiehajucej hodiny sa neuvadza ako prepad objemu (ZEC #175) |
| `test_macro_fuse` | poistka proti zhluku makro udalosti |
| `test_ada_fixes` | watch rationale pri spravnej urovni, konfrontacia s cerstvym zatvorenim, odlozeny verdikt (ADA #186) |

## Poznamka k historii

Testy vznikali 8/2026-9/2026 v scratchpade jednotlivych sessions a do repa sa
presunuli 2026-09-04 (holisticka kontrola). Tri sa vtedy zahodili, lebo
testovali uz neexistujuce veci: `test_fast_health` (minutovy health poller,
odstraneny 4.9.), `test_final` a `test_snapshot` (obe `market_regime`,
premenovany na `price_range` 31.8.).
