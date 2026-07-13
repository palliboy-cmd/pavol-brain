# Brain Direction Reassessment — Pavol-Brain ako dlhodobý externý mozog

- **Status:** Accepted — rozhodnutia pre M1 boli implementované a live-accepted na mini-core (2026-07-13)
- **Dátum:** 2026-07-13
- **Autor:** Claude (Fable 5) na základe zadania Pavla Pavlovského
- **Vstupy:** Proposals 001–008 · `spike/DECISION.md` · `sqlite-spike/DECISION.md` · `spike/schema/journal.sql` · `brain/` (kód) · `docs/operations/*` · `docs/integrations/*` · web rešerš 2026-07-13
- **Značenie tvrdení:** **[repo]** = overiteľný fakt z repozitára · **[web]** = externý zdroj, orientačne · **[návrh]** = odporúčanie tohto dokumentu, nie prijatý fakt

> **Stav po M1:** M1 uzavrel čítanie → práca → kontrolovaný zápis → handoff medzi agentmi. Potvrdené rozhodnutia a trvalý výsledok sú v [ADR-001](adr-001-m1-live-acceptance.md); ďalšie vedome odložené témy sú v [M2 backlogue](m2-roadmap.md).

---

## 1. Executive summary

Pavol-Brain je dnes ďalej, než sugeruje formulácia „prehodnotiť smer": **kanonický append-only SQLite journal, deterministický vector retrieval a read-only MCP server bežia naživo na mini-core a Hermes ich reálne používa** [repo]. Zjednodušený cieľ („dlhodobý externý mozog, ktorého sa agent opýta a zrekonštruuje kontext") nevyžaduje redesign — vyžaduje **dostavať jednu chýbajúcu polovicu: zapisovaciu cestu pre agentov**. Dnes nemá žiadny agent ako do Brainu zapísať výsledok svojej práce; MCP povrch je päť read-only nástrojov [repo].

Odporúčanie v jednej vete:

> **Ponechať malé vlastné jadro (append-only SQLite journal + derived vector index + MCP transport + review-first model), doplniť minimálnu zapisovaciu cestu (`brain_record_outcome`, `brain_record_decision` s write policy z Proposal 002 §10), rozšíriť payload rozhodnutí o zamietnuté alternatívy s podmienkami znovuotvorenia, kauzalitu modelovať typovanými linkami medzi recordmi, pridať `problem` a `analysis` ako prvotriedne typy — a API tvarovať podľa potrieb agenta, nie podľa databázového modelu: default workspace scope z profilu agenta, `brain_context(task)` ako plánovaná neskoršia fasáda.**

Externá rešerš (čerstvo auditovaná v Proposal 007, 2026-07-12, doplnená 2026-07-13) potvrdzuje, že žiadne hotové riešenie nespĺňa kombináciu append-only kanonická pravda + review-first zápis + workspace/sensitivity izolácia + deterministický rebuild. Graphiti zostáva uzavreté presnou formuláciou z DECISION.md; Obsidian a dashboard nie sú na kritickej ceste; knowledge loops sa odkladajú, kým prvý uzavretý okruh (agent číta → pracuje → zapíše → iný agent číta) nepreukáže hodnotu.

Greenfield test (§13) jadro potvrdil a skorigoval povrch: API sa tvaruje podľa potrieb agenta, default scope prichádza z profilu agenta, `problem`/`analysis` sú prvotriedne typy a izolácia sensitive/WORK dát sa rozhoduje **pred** write cestou — preferovane ako samostatná Personal a WORK inštancia.

## 2. Čo sme chceli pôvodne a čo chceme teraz

**Pôvodne** (Proposal 001–003): širšie riešenie — gateway k aplikáciám, temporálny knowledge graph (Graphiti/FalkorDB), embeddings, neskôr Graphify, projekcie, dashboard, Obsidian väzby, knowledge loops. Proposal 002 už tento rozsah raz zúžil („najprv jeden spoločný mozog, až potom nástroje a ruky") [repo].

**Teraz** (toto zadanie): Pavol-Brain je **dlhodobý externý mozog a spoločná pamäť pre Pavla a všetkých agentov**. Primárne rozhranie nie je ručné hľadanie v Markdown/Obsidiane, ale agent, ktorý z Brainu zrekonštruuje správny kontext. Brain má dlhodobo držať celý oblúk: problém → analýza → alternatívy → zamietnutia s dôvodmi → rozhodnutie → implementácia → výsledok → následky → neskoršie zmeny → platnosť pôvodných dôvodov. Systém má zabrániť slepému znovuotváraniu zamietnutých myšlienok, ale aj slepému rešpektovaniu zastaraných zamietnutí.

Rozdiel medzi „pôvodne" a „teraz" je menší, než vyzerá: journal-first architektúra z 002 bola navrhnutá presne pre tento cieľ. Čo sa mení, je **priorita**: z retrieval kvality (Slices 5–6) na **uzavretie okruhu čítanie-práca-zápis-čítanie**, a z infraštruktúry (graf, dashboard, projekcie) na **informačný model rozhodnutí a kauzality**.

## 3. Zhodnotenie aktuálneho stavu repozitára

Všetko v tejto sekcii je **[repo]**.

### Hotové a živé (nechať tak)

| Vrstva | Stav | Dôkaz |
|---|---|---|
| Kanonický journal | SQLite, append-only `memory_records` + `memory_events` + fold `record_state`, `artifact_links`, idempotencia, provenance (agent_id, session_ref, source_excerpt), supersede chains | `spike/schema/journal.sql`, 157 eventov na mini-core |
| Artifact validation | Relation-level append-only validácia (P008), review-first, `REBUILD_REQUIRED` namiesto hádania | migrované a backfillnuté 2026-07-12 |
| Retrieval | Vector-only exact cosine, deterministický ranking, filter-then-rank, zero-leak invariant, historical mode (`as_of`), rebuild s atomic switchom | `sqlite-spike/DECISION.md` (GO WITH CONDITIONS), parity 24/24 |
| Inkrementálna projekcia | Single-writer projektor, kurzor, projection hash, embedding cache, LaunchAgent každých 5 min | Slice 2+3 live evidence |
| MCP server | 5 read-only nástrojov (`brain_search`, `brain_get_record`, `brain_get_related`, `brain_health`, `brain_rebuild_status`), stdio/SSH-stdio, identita z launcheru, nie z requestu | `brain/mcp_server.py`, Hermes 0.18.2 acceptance PASS |
| Control plane | Control Center (loopback + SSH forward), integračný registry, append-only policy eventy | `docs/operations/brain-control-center.md` |

### Uzavreté (rešpektovať, neprepisovať)

- **Graphiti/FalkorDB:** „NO for the tested local Graphiti 0.29.2 + structured-output stack; graph retrieval quality was not evaluated." Re-open klauzula: významná nová verzia alebo preukázateľne spoľahlivý lokálny structured-output stack, bez rozšírenia patch budgetu. Spike má dodnes hodnotu ako presne zdokumentované zamietnutie — je to prototyp presne toho druhu pamäte, ktorú chce zadanie inštitucionalizovať.
- **Hybrid RRF:** disabled do Slice 5 re-merania; kód zostáva benchmark-only.
- **ChatGPT:** mimo MVP (R7 z P007).

### Chýbajúce voči novému cieľu (jadro tohto dokumentu)

1. **Zapisovacia cesta pre agentov neexistuje.** `brain.remember` / `record_decision` / `record_outcome` sú navrhnuté v P002 §8/§10, ale implementovaný je len read kontrakt z P006. Journal sa dnes plní iba spike ingest skriptami. Agent, ktorý dnes dokončí prácu, nemá kam zapísať outcome — celý cieľ „spoločná pamäť" stojí na tomto kroku.
2. **Zamietnuté návrhy nemajú prvotriednu reprezentáciu.** Status `rejected` v journale znamená „review zamietol candidate zápis" — nie „táto myšlienka bola posúdená a zamietnutá z dôvodu X, znovu posúdiť keď Y". Zamietnutie Graphiti žije v Markdown DECISION.md, nie v Braine.
3. **Kauzalita problém → … → následok nemá nosič.** Existujú supersede chains (record→record) a artifact_links (record→URI), ale žiadny typovaný link „decision D rieši problém P" / „outcome O implementuje decision D".
4. **Session outcome bez celého chatu:** typ `outcome` + `session_ref` + `source_excerpt` v scheme na to stačia, ale bez write cesty sa nepoužívajú.
5. **Otvorené podmienky:** S4 noise FAIL (10,53 %), Slice 5 (rozšírený benchmark + model bake-off, R1–R3 z P007) a Slice 6 (production label) nevykonané. Codex smoke FAIL (cancellation bug), Claude agent beh NOT EVALUATED.

### Slepé uličky a pozostatky

- `graph_edges` a `projection_map` v journal scheme sú pozostatky Graphiti spiku — nezavadzajú, ale pri najbližšej schémovej zmene ich označiť za deprecated [návrh].
- Obsidian/dashboard ako *produktové* rozhranie pamäte sa nikdy nezačali stavať — to je v súlade s novým cieľom, nie dlh.

## 4. Produktová definícia Pavol-Brain

**[návrh]** Pavol-Brain je **jediná dlhodobá, agentom čitateľná aj zapisovateľná pamäť Pavla a jeho agentov**:

- **Nie je to** poznámkový systém, druhý Obsidian, chat archív, ani knowledge graph ako cieľ sám osebe.
- **Je to** kanonický register toho, *čo sa riešilo, čo sa rozhodlo, čo sa zamietlo a prečo, čo sa vykonalo a s akým výsledkom* — s časovou platnosťou, provenance a kauzalitou.
- **Primárne rozhranie:** agent (Claude/Codex/Hermes/budúci), ktorý sa Brainu pýta a doň zapisuje cez úzky stabilný kontrakt. Človek číta Brain spravidla cez agenta; priamy prístup (CLI, Control Center) je operátorský, nie produktový.
- **Autorita:** aktuálny kód a autoritatívne dokumenty > Brain > domnienka agenta (kotva z P002 §5 platí ďalej).
- **Úspech sa meria jedinou otázkou:** musel Pavol agentovi znovu vysvetliť históriu, ktorú už raz niekto vyriešil? Ak áno, Brain zlyhal.

## 5. Hlavné use cases

1. **Handoff medzi agentmi:** Codex dokončí úlohu, zapíše outcome (čo, prečo, artefakty, otvorené otázky). Claude o týždeň začne nadväzujúcu úlohu volaním `brain_search` a nemusí sa pýtať Pavla.
2. **Ochrana pred re-otvorením zamietnutého:** agent navrhne „použime Graphiti" → search nájde decision record so zamietnutím, dôvodom a podmienkami znovuotvorenia → agent buď upustí, alebo doloží, že podmienky sú splnené.
3. **Kontrolované znovuotvorenie:** okolnosti sa zmenili (nový spoľahlivý structured-output stack) → agent nájde zamietnutie, overí reopen podmienku, navrhne nové posúdenie s odkazom na pôvodný záznam — nie od nuly.
4. **Ročná archeológia (INIT_ELAB scenár):** „čo sa v INIT_ELAB menilo, prečo, a môže dnešný problém byť následkom?" → reťaz problém → analýza → decision → outcome → artefakty (commity), aj rok dozadu, cez `brain_search` + `get_related` v historical mode.
5. **Rekonštrukcia kontextu pri štarte práce:** agent si pred úlohou vypýta platné rozhodnutia, posledné outcomes a otvorené otázky workspace-u.
6. **Korekcia bez straty histórie:** nové poznanie superseduje staré s dôvodom; historická otázka vráti pôvodný stav s vyznačenou neplatnosťou (už implementované pre čítanie [repo]).

## 6. Odporúčaná minimálna architektúra

**[návrh]** Žiadna nová infraštruktúra. Architektúra zostáva:

```
agenti (Claude / Codex / Hermes / …)
   │  MCP stdio / SSH stdio — READ (hotové) + WRITE (doplniť)
   ▼
brain kontrakt (Python library = sémantika; MCP = transport)
   │  write policy (pásma A/B/C z P002 §10) + secret filter
   ▼
kanonický SQLite journal (source of truth, append-only)
   │  single-writer projektor (hotové)
   ▼
derived retrieval index (vector-only, disposable, rebuildovateľný)
```

- **SQLite journal zostáva source of truth.** Áno — potvrdzuje to spike evidencia, externý audit (P007 §4.1) aj rešerš: event-sourced lokálny journal je najlepšia poistka proti vendor/model lock-inu a jediný formát, ktorý prežije výmenu čohokoľvek nad ním.
- **Markdown/Obsidian = len projekcia/export, nie autorita.** V MVP ani to nie — odkladá sa (viď §11).
- **Graphiti = voliteľná odvodená vrstva, dnes žiadna.** Journal umožňuje graf kedykoľvek doplniť ako ďalšiu projekciu popri vektoroch; re-open len podľa DECISION klauzuly.
- **Jeden nový kus:** write cesta = tie isté transporty, tá istá knižnica, nové operácie s policy. Žiadny server framework, žiadny event bus, žiadna queue.
- **Izolácia sensitive/WORK dát je potvrdená pred write cestou.** M1 používa **dve samostatné inštancie** — Personal Brain a WORK Brain (dva journaly, dva MCP profily). Zero-leak preto platí konštrukciou, nie iba policy testami. Cena: žiadne queries ponad Personal↔WORK hranicu — ktorá sa ani nemá prekračovať. Riadková sensitivity policy nebola zvolená ako hranica medzi inštanciami.

## 7. Minimálny informačný model

**[návrh]** Odpoveď na otázku „potrebujeme Observation, Episode, Decision, Rejected Option, Project, Agent Run a Link?": **nie ako nové tabuľky ani nové entity — existujúci model to unesie so štyrmi cielenými rozšíreniami** (z toho jedno je len rozšírenie type enum-u).

Existujúce typy [repo]: `decision`, `outcome`, `fact`, `preference`, `correction`, `artifact_link`. Mapovanie konceptov zo zadania:

| Koncept zo zadania | Nosič | Zmena potrebná? |
|---|---|---|
| Observation | `fact` (pásmo B = candidate, ak je to interpretácia) | nie |
| Episode / Agent Run | `outcome` + `session_ref`, `agent_id`, `source_excerpt` | nie (len write cesta) |
| Decision | `decision` | rozšíriť payload (nižšie) |
| Rejected Option | payload `decision` recordu | áno — bod 1 |
| Problem / Analysis | nové prvotriedne typy `problem`, `analysis` | áno — rozšírenie 0 |
| Project | `workspace` (+ voliteľný `fact` „project charter") | nie |
| Link | typované record→record linky | áno — bod 2 |

**Rozšírenie 0 — `problem` a `analysis` ako prvotriedne typy.** Kauzálna reťaz zo zadania je cieľ systému, nie okrajový prípad — zaslúži si typy v CHECK constrainte, nie rolu v payloade: jemnú payload sémantiku agenti klasifikujú nespoľahlivo, typ je lacný, filtrovateľný a viditeľný v retrievale. Enum sa rozširuje na `problem`, `analysis`, `decision`, `outcome`, `fact`, `preference`, `correction`, `artifact_link` pri tom istom schema_version bumpe ako rozšírenie 1.

**Rozšírenie 1 — rozhodnutie nesie alternatívy a zamietnutia.** Payload `decision` (schema_version bump, spätne kompatibilné):

```jsonc
{
  "statement": "Retrieval backend je SQLite vector-only.",
  "rationale": "…",
  "alternatives": [
    {
      "option": "Graphiti/FalkorDB",
      "verdict": "rejected",
      "reason": "lokálny structured-output stack nespoľahlivý; 5 patch iterácií; N5/N6",
      "reopen_when": "významná nová verzia Graphiti ALEBO spoľahlivý lokálny structured-output stack",
      "evidence": ["repo://pavol-brain/spike/DECISION.md"]
    }
  ]
}
```

Kľúčová sémantika: **zamietnutá alternatíva je súčasť platného, accepted decision recordu** — nájde sa bežným retrievalom (je v `canonical_text`), nie je to journal status `rejected` (ten zostáva vyhradený pre review-zamietnuté zápisy). `reopen_when` je text pre agenta, nie stroj — vyhodnocuje ho model pri čítaní, čo je presne tá rovnováha „neotváraj slepo / nerešpektuj slepo".

**Rozšírenie 2 — kauzalita cez typované record→record linky.** Reťaz problém → analýza → rozhodnutie → implementácia → výsledok → následok = malá uzavretá množina relácií: `addresses`, `analyzes`, `decides`, `implements`, `results_in`, `caused_by`, `supersedes` (existuje). Dve implementačné cesty, rozhodnúť pri návrhu write cesty:
- (a) rozšíriť `artifact_links` o `record://<record_id>` URI schému — nulová schémová zmena, `get_related` už tieto linky vracia; alebo
- (b) samostatná `record_links` tabuľka — čistejšie typovanie za cenu migrácie.
Odporúčam (a) pre M1 a (b) len ak sa (a) ukáže tesné.

**Rozšírenie 3 — časová platnosť a prekonanie: žiadna zmena.** `valid_at`/`invalid_at`, supersede chains, historical mode s `as_of` a pravidlo „historical widens time, not trust" [repo] pokrývajú historický fakt vs. aktuálny stav vs. prekonané rozhodnutie. Piata kategória zo zadania — „návrh vhodný na nové posúdenie" — je derivát: zamietnutá alternatíva, ktorej `reopen_when` môže byť splnené; to posudzuje čítajúci agent, nie stavový stĺpec.

## 8. Spoločný protokol pre agentov

**[návrh]** Protokol = MCP kontrakt + povinná disciplína v inštrukciách každého agenta (CLAUDE.md / AGENTS.md / Hermes profil):

1. **Pred netriviálnou úlohou čítaj:** `brain_search(query=<úloha>, types=["decision","outcome"])` v default scope svojho profilu; pri zásahu do staršej témy aj historical mode a `get_related`.
2. **Pred návrhom smeru hľadaj zamietnutia:** ak search vráti decision so zamietnutou alternatívou zhodnou s návrhom, agent musí buď upustiť, alebo explicitne argumentovať splnenie `reopen_when` — citujúc record_id.
3. **Po dokončení úlohy zapíš outcome:** čo, prečo, kľúčové rozhodnutia, artefakty (commit SHA, súbory), otvorené otázky. Pásmo A vyžaduje overiteľný artefakt; inak candidate (pásmo B).
4. **Rozhodnutia zapisuj len potvrdené** (`record_decision` so statement + rationale + alternatívami); interpretácie idú ako candidate.
5. **Provenance sa cituje, neparafrázuje** — record_id v odpovediach agenta, aby sa dalo overiť a nadviazať.
6. **Konflikt s realitou:** ak Brain tvrdí X a kód/dokumenty ukazujú inak, platí kód; agent zapíše korekčný candidate.

**Kontrakt v2 — scope z profilu [návrh]:** default workspace scope každého volania sa odvodzuje z profilu agenta (Control Center registry, kde už allowlisty žijú [repo]); per-call parameter `workspaces` smie scope **len zúžiť** — pokus o rozšírenie je validation error. Povinné `workspaces` na každom volaní z P006 §5 sa tým ruší: izoláciu vynucuje launcher a profil, nie parameter; per-call povinnosť bola voči profilovým grantom duplicitná a pridávala trenie každému volaniu. Sensitivity pravidlá (grant + explicitnosť) sa nemenia.

Transport je pre všetkých rovnaký (MCP cez Control Center profily, identita z launcheru [repo]) — protokol je model- aj vendor-nezávislý; nový agent = nový profil + tie isté inštrukcie.

## 9. Capture a relevance stratégia

**[návrh]**

- **Zachytávať:** potvrdené rozhodnutia s alternatívami; outcomes sessionov (štruktúrované, s artefaktmi); korekcie; trvalé preferencie; problémy/analýzy pri väčších témach. Zápis robí agent na konci práce — **jeden štruktúrovaný zápis per session/úloha**, nie priebežný stream.
- **Ignorovať ako šum:** transkripty, chain-of-thought, stack traces, medzikroky, neoverené hypotézy ako `fact`, tajomstvá (deny filter z P002 pásma C).
- **Minimalizácia manuálneho písania:** v M1 zapisuje agent na pokyn v inštrukciách (žiadna Pavlova ruka). Automatické hooky (napr. Claude Code SessionEnd → návrh outcome candidate) sú fáza 2 — až keď sa ukáže, že agenti disciplínu nedodržiavajú.
- **Relevancia pri zápise, nie pri čítaní:** pásma A/B/C rozhodujú vstup; dôveryhodnosť ďalej nedegradujeme skóre. Postupná relevancia podľa používania (usage counters) je knowledge-loop téma — odložená (§ non-goals). Štyri-týždňový usage checkpoint [repo] zostáva jediným meradlom.

## 10. Retrieval a rekonštrukcia kontextu

- **Prvá verzia potrebuje:** vector search + metadata filtre (workspace/typ/čas) + explicitné linky — **presne to, čo je postavené** [repo]. Fulltext zostáva diagnostika, graf žiadny, časové filtre existujú (historical mode).
- **Rekonštrukcia kontextu:** v M1 kompozícia na strane agenta (2–4 volania: search decisions, search recent outcomes, get_related) podľa receptu v protokole §8. **`brain_context(task)` je plánovaná task-shaped fasáda** [návrh]: API má byť dlhodobo tvarované podľa potreby agenta („daj mi kontext pre úlohu X"), nie podľa databázového modelu, a fasáda zakóduje rekonštrukčnú disciplínu raz namiesto v inštrukciách každého agenta zvlášť. **Nesmie však blokovať M1** — navrhne sa po M1 podľa reálne pozorovaných kontextových otázok agentov (zaradenie: M3).
- Slice 5 (benchmark ≥100 queries, 10× distractors, model bake-off nomic v1 / Nomic V2 / BGE-M3 / Qwen3-Embedding) zostáva v pláne **po** M1 — kvalita retrievalu je dnes dobrá na desiatky recordov; hodnotu teraz blokuje absencia obsahu, nie presnosť [návrh, opiera sa o P007 DA-8].

## 11. Úloha dashboardu a Obsidianu

- **Control Center = operátorská konzola, nie produkt.** Zostáva na správu integrácií, policy a health; **feature-freeze** — žiadne prehliadanie pamäte, žiadne review UI v M1 (review kandidátov pôjde cez CLI, prípadne cez agenta) [návrh]. Greenfield test (§13): od nuly by Control Center nevznikol, stačil by config súbor — freeze je preto trvalý strop, nie odklad.
- **Obsidian = ľudská znalostná vrstva, nie backend ani povinná projekcia.** Potvrdzuje sa P002. Prenosný export/audit rieši samotný journal (SQLite súbor + dokumentovaná schéma = migrovateľné dáta). Markdown projekcia journalu (read-only export pre vault) je lacná a užitočná **až keď je čo čítať** — zaradiť ako voliteľný míľnik M3+, nie skôr [návrh].
- **Knowledge loops (kompilácia, sumarizácia, spätné hodnotenie):** teraz nie. Prvý loop má zmysel až nad reálnym obsahom a reálnym používaním.

## 12. Porovnanie relevantných existujúcich riešení

P007 vykonal audit 2026-07-12 [repo]; doplnené o rešerš 2026-07-13 [web]:

| Riešenie | Čo je | Prečo nie ako základ |
|---|---|---|
| [Mem0/OpenMemory](https://github.com/mem0ai/mem0) | plochá extrahovaná pamäť, MCP | self-edit prepisuje namiesto supersede; bez review, bez auditu |
| [Zep/Graphiti](https://github.com/getzep/graphiti) | temporálny KG, silný na LongMemEval [web] | vyžaduje spoľahlivý structured-output LLM; lokálny stack zlyhal [repo]; re-open klauzula platí |
| [Letta (MemGPT)](https://github.com/letta-ai/letta) | stateful agent runtime | pamäť viazaná na ich runtime — opak požiadavky vendor-nezávislosti |
| [Cognee](https://github.com/topoteretes/cognee) | ETL dokumenty→graf+vektory | dávkový korpus engine, nie interakčná pamäť s review |
| [RedPlanetHQ CORE](https://github.com/RedPlanetHQ/core) | „one brain, many agents", temporálny KG, MCP | ideovo najbližší; ťažký (8 GB), AGPL, bez review-first; watchlist |
| [Basic Memory](https://github.com/basicmachines-co/basic-memory) | Markdown+SQLite MCP pamäť, local-first | najbližší duchom; bez temporality/supersede, bez workspace izolácie, bez append-only auditu |
| agentmemory, claude-mem, codebase-memory-mcp [web] | session/codebase pamäť pre coding agentov | per-repo alebo per-tool pomôcky; bez kanonického journalu, provenance a policy; claude-mem je Claude-centrický |

**Posúdenie použitia (nie len zoznam):** ako *komponent* nie je čo prevziať — kritické vlastnosti (append-only journal, review-first, izolácia, determinizmus) sú presne tie, ktoré externé projekty nemajú, a zvyšok (cosine search, MCP server) je už napísaný a otestovaný lokálne. Ako *adaptácia* by Basic Memory alebo CORE znamenali vzdať sa hotového, validovaného jadra výmenou za cudzí dátový model bez temporality/policy. Ako *inšpirácia* áno: claude-mem-štýl session capture pre fázu 2 hookov; CORE sledovať ako konkurenčný dizajn.

## 13. Greenfield test

Kontrolná otázka pred finalizáciou: **keby dnes neexistoval ani riadok kódu Pavol-Brain, navrhli by sme to rovnako?** [návrh]

**Čo konverguje aj od nuly — a preto sa zachováva bez zmeny:** append-only SQLite journal ako pravda, typed records s provenance, temporalita so supersede, derived rebuildovateľný vector index, MCP transport, review-first zápis, local-first. To je event-sourcing jadro, ku ktorému by dospel aj čistý návrh; vector-only route je navyše podložená meraním (FTS neprešiel top-3 gate [repo]), nie zvykom.

**Čo by greenfield urobil inak — premietnuté ako korekcie do §7, §8, §15 a §16:**

1. **API tvarované podľa potrieb agenta, nie databázy.** Agent potrebuje dve slovesá: „daj mi kontext pre X" a „toto sa stalo". Dnešný search kontrakt je retrieval-inžiniersky povrch z benchmark éry. → Korekcia: write slovesá v M1; `brain_context(task)` ako plánovaná task-shaped fasáda (M3, neblokuje M1).
2. **Scope z profilu, nie z parametra.** Povinné `workspaces` na každom volaní duplikuje per-profil allowlisty; izoláciu vynucuje launcher. → Korekcia: default scope z profilu agenta, volanie smie scope len zúžiť (§8, kontrakt v2).
3. **`problem` a `analysis` ako prvotriedne typy** namiesto payload roly — kauzálna reťaz je cieľ systému. → Korekcia v §7, rozšírenie 0.
4. **Izolácia sensitive/WORK dát inštanciou, nie riadkovou policy.** Samostatná Personal a WORK inštancia = zero-leak konštrukciou a menej policy mašinérie; cena je nemožnosť queries ponad hranicu, ktorá sa aj tak nemá prekračovať. → Rozhodnúť pred write cestou (§16, krok 0); preferovaný variant sú samostatné inštancie.
5. **Poradie výstavby.** Greenfield by staval capture pred retrieval kvalitou — hodnota mozgu je akumulovaný obsah, nie presnosť nad 51 recordmi. → M1 to koriguje; Slice 5–6 až po ňom.

**Trvalý test jednoduchosti:** pri každej budúcej vlastnosti položiť otázku *„zvládol by to git repozitár s Markdown konvenciou?"* Markdown mozog by pri dnešnom objeme doručil väčšinu hodnoty za zlomok nákladov; prehráva len na štyroch prednostiach journalu: vynútiteľnosť write policy, temporalita na úrovni faktu, tvrdá izolácia a deterministický provenance retrieval. **Vlastnosť, ktorá nestojí aspoň na jednej z týchto štyroch predností, do Brainu nepatrí.** (Control Center by týmto testom neprešiel; `brain_context` ním prejde len ako tenká fasáda nad existujúcim retrievalom.)

## 14. Varianty

**V1 — Malé vlastné jadro + write cesta (dostavať existujúce).** Doplniť 2 write operácie + payload alternatív + record→record linky. Náklad: dni, nie týždne; nulová nová infra; nadväzuje na validované jadro. Riziko: vlastná údržba (DA-4 z P007) — mitigované malým scope a kill kritériami.

**V2 — Adaptácia existujúceho riešenia (Basic Memory alebo CORE ako základ, vlastné dáta migrovať).** Náklad: migrácia + strata review-first/izolácie/determinizmu + AGPL (CORE) alebo chýbajúca temporalita (Basic Memory); zisk: cudzia údržba retrieval vrstvy. Zamietam: vzdáva sa presne tých vlastností, kvôli ktorým projekt existuje, a zahodí validovanú prácu [návrh].

**V3 — Hybrid (vlastný journal ako pravda + externý memory systém ako retrieval/UX vrstva, napr. Mem0 alebo Graphiti projekcia).** Journal by ostal autoritou a externý systém by bol disposable index — architektúra to výslovne umožňuje. Zamietam **pre teraz**: druhý systém na údržbu bez preukázanej potreby; presne tento tvar už raz zomrel na Graphiti spike nákladoch. Zostáva legitímna budúca cesta, ak vector-only kvalita prestane stačiť (Slice 5 to zmeria).

**Odporúčanie: V1.** Jediný variant, ktorý zväčšuje hodnotu bez zväčšenia prevádzkovej plochy.

## 15. Odporúčanie (jedno, jasné)

**Dostavať malé vlastné jadro o zapisovaciu cestu a model rozhodnutí; API tvarovať podľa potrieb agenta. Append-only SQLite journal, derived vector index, MCP transport a review-first model sa nemenia.** Konkrétne:

0. pred write cestou rozhodnúť izoláciu sensitive/WORK dát — preferovaný variant na zhodnotenie: samostatná Personal a WORK inštancia Brainu (§6, §13 bod 4);
1. `brain_record_outcome` + `brain_record_decision` cez existujúci MCP server s write policy pásmami A/B/C;
2. decision payload s `alternatives[].{verdict,reason,reopen_when,evidence}`;
3. record→record linky cez `record://` URI v `artifact_links`;
4. type enum rozšíriť o `problem` a `analysis` (ten istý schema bump);
5. kontrakt v2: default workspace scope z profilu agenta, volanie ho smie len zúžiť;
6. agent protokol (§8) do inštrukcií Claude/Codex/Hermes;
7. spätne zapísať 5–10 kľúčových historických rozhodnutí (Graphiti NO, vector-only GO, hybrid disabled, D1–D10 z P007) ako prvý reálny obsah.

`brain_context(task)` sa **eviduje ako plánovaná task-shaped fasáda** (M3) — neblokuje M1. Všetko ostatné (Slice 5–6, Obsidian export, hooky, knowledge loops) až po dôkaze používania.

## 16. Inkrementálny implementačný plán

- **M1 — Uzavretý pamäťový okruh** (**dokončený a live-accepted 2026-07-13**; zámerne úzky — jediný cieľ je uzavrieť tok agent číta → pracuje → zapisuje → iný agent pokračuje):
  0. **Rozhodnutie o izolácii (pred kódom):** samostatná Personal a WORK inštancia (preferované; dva journaly, dva MCP profily, zero-leak konštrukciou) vs. ponechanie riadkovej sensitivity policy. Výsledok sa po kroku 1 zapíše ako prvý decision record cez nové API (bootstrap obsahu).
  1. Write kontrakt v `brain` knižnici: `record_outcome`, `record_decision` (+ candidate pásmo pre `remember`), validácia, secret filter, idempotencia — journal už všetko unesie.
  2. Schema bump: decision payload v2 (alternatívy/reopen), type enum + `problem`/`analysis`, `record://` linky.
  3. Kontrakt v2 scope: default z profilu agenta, per-call len zúženie (read aj write).
  4. MCP write nástroje za per-profil grantom v Control Center registry (default: write disabled). Projektor bez zmeny (nové recordy zje existujúci cursor flow).
  5. Backfill historických rozhodnutí (bod 7 z §15) cez nové API — zároveň prvý reálny test.
  6. Agent inštrukcie (Claude/Codex/Hermes) s protokolom §8.
  7. **Akceptačný tok:** Claude si načíta kontext → vykoná úlohu → zapíše outcome+decision → Hermes/Codex o deň neskôr načíta a pokračuje bez vysvetľovania histórie. Zdokumentovať ako live evidence (štýl `sqlite-spike/results/`).

  Mimo M1 (nesmie doň pritiecť): `brain_context`, review CLI, hooky, Obsidian export, Slice 5.
- **M2 — Review slučka a disciplína:** CLI review kandidátov, audit write path, usage checkpoint po 4 týždňoch; oprava Codex smoke; Claude agent evaluácia.
- **M3 — Kvalita a fasáda (podmienené M1/M2 dôkazom):** `brain_context(task)` navrhnutá podľa reálne pozorovaných kontextových otázok agentov; Slice 5 benchmark + model bake-off; voliteľný Markdown/Obsidian export.

## 17. Proof-of-usefulness kritériá

Po 4 týždňoch od M1 (rozšírenie existujúceho checkpointu [repo]):

1. ≥ 15 outcome/decision recordov zapísaných agentmi (nie backfill).
2. ≥ 5 zdokumentovaných prípadov, keď agent zrekonštruoval kontext z Brainu bez Pavlovho vysvetľovania (record IDs v transkripte).
3. ≥ 1 prípad, keď search zabránil re-otvoreniu zamietnutej myšlienky, alebo ju korektne znovu otvoril cez `reopen_when`.
4. Údržba < 0,5 h/týždeň; zero-leak invariant drží aj na write ceste.
5. **Kill kritérium (z P001/P002, prenesené):** ak 1.–2. nenastanú, projekt sa zastavuje alebo zmenšuje — bez výnimky pre „ale je to pekne postavené".

## 18. Explicitné non-goals

- Ukladanie konverzácií, transkriptov, chain-of-thought.
- Graf/Graphiti oživenie mimo DECISION re-open klauzuly; žiadny supergraf.
- Obsidian ako backend; povinná Markdown projekcia v M1–M2.
- Dashboard ako produktové UI pamäte; nové Control Center funkcie.
- Knowledge loops, automatická sumarizácia, usage-based relevancia.
- ANN/vector DB, hybrid RRF v API, ranking tuning pred Slice 5.
- Verejný endpoint, ChatGPT, multi-user, cloud.
- Automatické hooky na capture (fáza 2, až po dôkaze nedisciplíny).
- `brain_context` v M1 — eviduje sa ako plánovaná task-shaped fasáda (M3), M1 neblokuje ani nerozširuje.

---

## Záver

- **M1:** uzavretý a prijatý; jeho architektonické rozhodnutia sú normatívne zaznamenané v [ADR-001](adr-001-m1-live-acceptance.md).
- **Ďalší krok:** vedome vyberať iba z otvoreného [M2 backlogu](m2-roadmap.md), bez spätnej zmeny rozsahu M1.

*Tento dokument zostáva rozhodovacím podkladom, ktorého M1 závery boli potvrdené live acceptance.*
