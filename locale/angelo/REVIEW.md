<!-- SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd. -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# What a native reviewer should check first

Every catalogue in `po/` (and `src/ar.po`) is machine-produced and pending native review. The translator of each
language flagged the choices below as the ones most worth a native speaker's eye: uncertain terminology,
ambiguous English, tight widths. Check them first, then read the catalogue in context with the GUI suite's
screenshots (`test_blender_translation.py`, `doc/angelo/localization.rst`). When a language is signed off, set
its header to `X-Angelo-Translation: reviewed by <name> on <YYYY-MM-DD>` and delete its section here.

Shared across languages:

* The approval-mode names (Ask before changes, Auto · bounded edits, Read only; the composer's Ask for approval,
  Read only, Approve for me) and the reasoning-effort scale (Minimal, Low, Medium, High, Extra high, Max, Ultra)
  are read as sets: check that each set is consistent and ordered.
* The web console's page names (People, Keys) are translated like the in-app items that open them; if the console
  itself stays English, use its English words in sentences that send people there.
* Bake types are Blender's own words: Blender's catalogue answers the default context first, so the Bake panel and Angelo's job cards always agree, mistranslations included. A better word goes to Blender's catalogue upstream.
* "Published" in the job messages means the finished output was moved into the chat's file workspace, not made public. Most languages say delivered, saved or output; pt, pt-BR, nl, sv, ar, hi and th use their word for publish, which a reviewer may change to one of those.
* Product names stay in Latin letters: Angelo, Blender, Claude, ChatGPT, Codex, OpenAI, Anthropic, and the
  feature name Media Studio.
* Sculpt checkpoint Restore notices: "checkpoint" is the language's restore point (the glossary's snapshot) and
  "seal key" a signing key (Russian ключ подписи). `{addons}` is a comma-separated list of add-on names, one or
  several, so the sentence around it must read with either; "load handlers" are the add-ons' code that runs when a
  file opens.

## Arabic (`ar`, `po/ar.po`)

- Cancel → ألغ (Blender's own rendering); most Arabic apps say إلغاء.
- Preferences → التفضيلات, but Blender's Arabic catalogue leaves "Preferences" in English, so "التفضيلات > مزودو الذكاء الاصطناعي" may not match Blender's menu.
- AI Providers → مزودو الذكاء الاصطناعي, quoted unchanged inside sentences; inflected in "Open AI Providers" (افتح مزودي الذكاء الاصطناعي).
- macOS and Windows names: Keychain سلسلة المفاتيح, Keychain Access الوصول إلى سلسلة المفاتيح, Credential Manager إدارة بيانات الاعتماد; keyring حلقة المفاتيح (literal).
- Prompt → موجه (Microsoft uses مطالبة); Dismiss → إخفاء; Dock Left/Right → إرساء إلى اليسار/اليمين.
- Blender's catalogue misspells some imperatives (إفتح، إستورد، إبحث): Angelo uses the correct spellings.
- Dual after a numeral ("Apply {count} change", two → طبق {count} تغييرين) and byte plurals (بايت/بايتات/بايتًا/بايتان): confirm the style.
- Effort levels: منخفض/متوسط/مرتفع/مرتفع جدًا/الأقصى/فائق/أدنى; seconds abbreviated ث.
- The editor-menu category "AI" → الذكاء الاصطناعي is about 7.6× the English width.
- Dialog fields: Imperial/Metric → «إمبراطورية»/«مترية» (Blender's feminine forms); Unit scale → «مقياس الوحدات»; flow scopes «العقد السابقة»/«العقد اللاحقة».
- Updates and crash reports: crash report → تقرير التعطل and "Angelo quit unexpectedly" → تم إنهاء Angelo بشكل غير متوقع; times "منذ {count} ي / س / د" (the day abbreviation ي is the least established); "Last checked {when}." → آخر تحقق: {when}.; the privacy promise ولا يحتوي أبدًا على مشاهدك… must be exactly as strong as the English.
- Background jobs: Blender's own Arabic bake-type words (Diffuse → اللون, Emit → ولّد, Transmission → الانبعاث) mean colour, generate and emission, and Blender's catalogue shows them first; Ambient Occlusion keeps Blender's spelling الإنحباس; cache → ذاكرة مؤقتة with full plural forms; check "kept {done} of {count} frames" for every count.
- Job publication errors: link or junction → رابط أو نقطة وصل; staging folder → مجلد التجهيز; Missing → مفقود; "لم يُنشر" reads as made public (see the shared note).

## German (`de`, `po/de.po`)

- Generate → Generieren (standard for AI generation; Blender's modifier wording is Erzeugen).
- Director → Regie, Director Mode → Regiemodus (could stay untranslated as a product name).
- seat → Lizenzplatz (no fixed German SaaS term; alternatives Sitzplatz, Lizenz, Seat).
- collaborator → Beratermodell; read-back → Rücklesung (technical coinage); staged → vorbereitet (Git German: vorgemerkt).
- Width: Undo restore → Wiederherstellung rückgängig (about 2.2× the English on a button); Load Saved Key from Keychain → Gespeicherten Schlüssel aus Schlüsselbund laden (about 1.7×); Enable Fast → Schnellmodus aktivieren.
- Console page names (Keys, People) are translated as Schlüssel, Personen: if the web console stays English, keep its words.
- Placeholder fills: "your keyring" → Ihrem Keyring (dative, fills "in {name}"); "Signed in to {org}." → Angemeldet: {org}.
- Sculpt {object} → {object} formen, sculpt pass → Skulpturdurchgang: check against Blender's Skulpturmodus wording.
- Spacebar Action → Leertasten-Aktion, Mouse Select → Mausauswahl: Blender's German catalogue (35%) has no entry; check against the splash.
- Dialog fields: Imperial → Britisch (Blender's Units panel, debatable); Upstream/Downstream → Vorgelagert/Nachgelagert; Brief "Constraints" → Einschränkungen.
- Updates and crash reports: Release notes → Versionshinweise (Blender's German says Herausgabehinweise); Got it → Verstanden; {count} d ago → vor {count} Tg.; View Last Report Sent → Zuletzt gesendeten Bericht anzeigen (long).
- Background jobs: Blender's German bake types show first (Transmission → Übergang is a known Blender mistranslation; Emit → Emitter; Normal → Normalen); "Nothing was published." → Es wurde nichts bereitgestellt.; job → Auftrag; time budget → Zeitbudget.
- Job publication errors: Windows "junction" kept as Junction; staging folder → Staging-Ordner; "Missing: {names}" → Fehlend: {names}.

## Spanish (`es`, `po/es.po`)

- Do anything → ¿Qué desea hacer?; You're here → Modo actual (badge).
- Composer approval modes: Aprobar por mí / Pedir aprobación / Solo lectura ("Aprobar automáticamente" is clearer but 1.6× wider).
- "Use Enter API Key …" quotes «Introducir clave de API», a label guessed from the English (the operator is "Clave de API").
- Project Brief → Ficha del proyecto; prompt kept as "prompt"; seat → puesto (alternative licencia).
- "this computer" → este equipo, but team is also equipo: team sign-out messages use dispositivo.
- Keyring → Depósito de claves (GNOME's term, 2.4× wide as a title); Refresh → Refrescar (Blender's word; Actualizar is more common).
- Add → Agregar, Continue → Continuar (Blender's plain words come from other domains).
- Welcome to Angelo → Le damos la bienvenida a Angelo; Contact your admin → Consulte a su administrador.
- Width: Copiar enlace de inicio de sesión (1.9×), Se usó una herramienta (2.1×), Ejecutar (2.5×).
- Dialog fields: Upstream/Downstream → Anteriores/Posteriores; (opt-in) → (opcional).
- Updates and crash reports: crash report → informe de fallos (Blender's Spanish says cierre inesperado for the crash); no longer supported → ya no recibe soporte; Release notes → Notas de la versión (Novedades kept for What's New).
- Background jobs: Blender's verbs Procesar (render) and Capturar (bake); Glossy → Reflectividad, Diffuse → Difusión; time budget → Límite de tiempo; Physics → Dinámicas; "Nothing was published." → No se entregó nada.
- Job publication errors: link or junction → enlace o unión; staging folder → carpeta de preparación; Missing → Faltan.

## French (`fr`, `po/fr.po`)

- Preview → aperçu (Blender: Prévisualisation; kept for "Material preview").
- Discard → Abandonner (Blender's Ignorer would read as "leave pending").
- Undo → Défaire / annulation ("Blender Undo" → Défaire de Blender; "one Undo step" → une seule étape d’annulation).
- seat → siège (alternative licence); prompt kept as "prompt" (Microsoft-style: invite).
- Director Mode → mode Réalisateur, Manual Mode → Mode Manuel: check as names.
- key store → coffre de clés; Keychain → trousseau / Trousseau d’accès; Windows → Gestionnaire d’identification; keyring kept.
- Coding Agent → Agent de code (and "abonnement de code"); Policy → Règle (managed policy → stratégie).
- Typography: U+00A0 before : ; ? ! % and inside « »; ’ apostrophe.
- Dialog fields: (opt-in) → (sur option); Connect with → Connexion via; Unit scale → Échelle d’unité (Blender).
- Updates and crash reports: Angelo quit unexpectedly → Angelo s’est fermé de manière inattendue (macOS: « a quitté de manière imprévue »); crash → plantage, as Blender's French; Update checks are off → Recherche de mises à jour désactivée.
- Background jobs: Blender's French bake types (Glossy → Brillant, Emit → Émission, Normal → Normale); frames are feminine as in Blender; time budget → Durée maximale; "Nothing was published." → Rien n’a été livré.
- Job publication errors: link or junction → lien ou jonction; staging folder → dossier de préparation; Missing → Manquant.

## Hindi (`hi`, `po/hi.po`)

- Blender's own Sanskritized terms are kept where Blender uses them: Node → ग्रन्थि, File → संचिका, Camera → प्रतिबिंबक, Resolution → बिंदू विस्तार, Default → मूलभूत, Copy → प्रतिलिपि. Modern Hindi software says नोड/फ़ाइल/कैमरा/रिज़ॉल्यूशन/डिफ़ॉल्ट; decide whether Angelo's own text should follow Blender or common usage.
- Do anything → "क्या करना चाहेंगे?" (polite question, shortened for width).
- Undo restore → पुनर्स्थापना पूर्ववत् करें (about 2.4× the English); Use → उपयोग करें (about 3.2×).
- Run {count} → रन {count} (the English is ambiguous).
- Read-back → पुष्टि-पठन (coined); An agent turn → एजेंट का एक दौर.
- OS key-store approvals use अनुमति, the agent's approval uses मंज़ूरी, on purpose.
- Director Mode → डायरेक्टर मोड beside Blender's Sculpt Mode → शिल्प रूप.
- View (menu) → दृश्य is also Blender's word for Scene; "3D view" / "Current view" use व्यू.
- Indian digit grouping in literal numbers: 655,360–8,294,400 → 6,55,360–82,94,400; 300,000 → 3,00,000.
- Keychain Access → कीचेन ऐक्सेस: check Apple's Hindi spelling.
- Dialog fields: flow-run scopes Upstream / Downstream / Node → पहले के चरण / बाद के चरण / यह ग्रन्थि; the confirmations are phrased without grammatical gender (मेरी पुष्टि है कि…, मैंने … की समीक्षा कर ली है).
- Updates and crash reports: crash report → क्रैश रिपोर्ट (loanword, also the heading); "files, file names" → संचिकाएँ, संचिका नाम (Blender's word, stiff in a privacy sentence); "System" kept in Latin as Blender's Hindi shows it; "Got it" → ठीक है (gender-neutral).
- Background jobs: Frame → गठन (Blender's word, obscure here); job → कार्य; simulation → सिमुलेशन; bake types Blender lacks: संयुक्त (Combined), परिवेशी अवरोधन (AO), परिवेश (Environment), संचरण (Transmission); units spelled out (से would read as "from").
- Job publication errors: link or junction → लिंक या जंक्शन; staging folder → स्टेजिंग फ़ोल्डर; Missing → अनुपलब्ध; "प्रकाशित" reads as made public (see the shared note).

## Indonesian (`id`, `po/id.po`)

- Apply → Terapkan (Blender's "Setujui" means Approve); Undo → Urungkan (Blender's Indonesian shows "Undo" in English); Global Undo kept English as Blender shows it.
- Outliner → Penggarisan Luar (Blender's term, awkward); object → benda (Blender's; "objek" is more common).
- Project Brief → Ringkasan Proyek (Summary is also Ringkasan); seat → kursi (alternative lisensi); Coding Agent → Agen Pengodean.
- Keychain / Keychain Access → Rantai Kunci / Akses Rantai Kunci (from Apple's Indonesian; confirm current macOS).
- Falloff kept in English (Blender's "Jatuh" is meaningless alone).
- Dialog fields: Upstream/Downstream → Hulu/Hilir (formal; "Langkah sebelumnya/berikutnya" plainer); Capture → Pengambilan.
- Updates and crash reports: crash report → laporan crash (loanword; laporan kerusakan is the alternative, about 20 strings); "Last checked {when}." → Pemeriksaan terakhir: {when}.; What's New → Yang baru; check the privacy promise «Laporan ini tidak terkait dengan Anda atau akun Anda.»
- Background jobs: bake as the loanword (mem-bake / di-bake; Blender's Indonesian mixes Panggang, Bakar and Bake); Transmission → Transmisi (Blender's entry is a mistranslation); Shadow → Bayangan; "Nothing was published." → Tidak ada hasil yang disimpan.
- Job publication errors: link or junction → tautan atau junction; staging folder → folder persiapan; Missing → Hilang.

## Italian (`it`, `po/it.po`)

- Coding Agent → Agente di codifica (Microsoft/GitHub Italian; "programmazione" is clearer but about 2× wider).
- reasoning effort → profondità di ragionamento; Low/Medium/High are shared with Media Studio quality, so feminine Bassa/Media/Alta.
- Stop → Interrompi (wider than Stop), named the same way in every sentence.
- Key-store names fill "in {name}" without articles (Keyring, Archivio chiavi di sistema, Portachiavi login di macOS); "{title}: …" avoids gender agreement.
- Team fills restructured to read with a real name or "il tuo team" ("spazio del team: {organization}").
- Director → Regista / Modalità Regista; snapshot → istantanea; Undo restore → Annulla ripristino ("Blender Undo" → Annulla di Blender).
- seat → postazione (alternative licenza); Welcome to Angelo → Ti diamo il benvenuto in Angelo.
- Delete {path} → Cancella {path} (Blender's word); elsewhere eliminare.
- Dialog fields: "Prima della prima modifica approvata (facoltativo)" repeats prima; Unit scale → Scala unità; Upstream/Downstream → A monte/A valle.
- Updates and crash reports: crash report → rapporto sull'arresto anomalo, the established term but long (5 width warnings; the loanword crash is the alternative); one possessive covers the privacy list (le tue scene, prompt, chat, file o chiavi); {count} g fa / {count} h fa.
- Background jobs: Blender's Italian uses Annulla for both Undo and Cancel, so the file promises say "annulli l'attività" for Cancel; Ambient Occlusion → Occlusione Ambientale; job → attività; "Nothing was published." → Non è stato consegnato nulla.
- Job publication errors: link or junction → collegamento o giunzione; staging folder → cartella di preparazione; Missing → Mancanti.

## Japanese (`ja`, `po/ja.po`)

- Accept → 確定 (kept apart from Approve 承認 and Apply 適用; alternative 採用).
- Effort scale: 最小/低/中/高/超高/最大/ウルトラ; 超高 is unusual on its own.
- Project Brief → プロジェクト概要 (avoids ブリーフ); "public brief" → 公開概要.
- collaborator → 協力モデル (alternative コラボレーター); read-back → 読み戻し (alternative 読み取り確認).
- Staged changes → ステージ済みの変更 (git-style; may confuse artists).
- Manual Mode → マニュアルモード (also reads "handbook"; alternative 手動モード, must stay identical everywhere).
- AI Enhance → AI 強化, enhancer → 強化モデル.
- Approval modes as a set: Approve for me 自動で承認, Do anything 何でも実行, Ask before changes 変更前に確認.
- You're here → 現在のモード (welcome-screen badge); Dismiss → 閉じる (same as Close).
- Native Subscription Sign-In → about 1.5× the English width on a button.
- Dialog fields: Capture → 取得タイミング (not Blender's キャプチャ); Imperial / Unit scale use Blender's 英単位系 / 単位の倍率; flow-run scopes 上流 / 下流.
- Updates and crash reports: Got it → 了解しました (or OK?); "Last checked {when}." → 最終確認：{when}; Ask Me Each Time / Always Send / Never Send → 毎回確認する / 常に送信する / 送信しない, and Don't send is also 送信しない; Sorry about that → 申し訳ありません。 (shortened).
- Background jobs: Blender's Japanese bake types 伝播 (Transmission) and 統合 (Combined) read oddly; "Nothing was published." → 何も出力されていません (no output produced); frame ranges use 〜.
- Job publication errors: link or junction → リンクまたはジャンクション; staging folder → ステージングフォルダー; Missing → 見つかりません.

## Korean (`ko`, `po/ko.po`)

- Discard → 폐기 (Blender's 포기 reads as "give up").
- Global Undo → 글로벌 되살리기 (Blender's own label for that preference, kept so it can be found).
- Aspect ratio → 종횡비 (Blender: 측면 비율); seat → 시트 (alternative 라이선스).
- collaborator → 협업 모델; staged → 적용 대기 / 스테이지; read-back → 다시 읽은 결과 (coined).
- Composer modes: Approve for me → 자동 승인, Ask for approval → 승인 요청: check they read as modes.
- Pop Out Chat → 채팅 창 분리; Dismiss → 무시 (alternative 닫기); Welcome to Angelo (menu) is long.
- Console page names translated (키, 구성원): keep English if the console is English.
- Effort levels: 최소/낮음/중간/높음/매우 높음/최대/울트라.
- Units options copy Blender's 메트릭(미터법) / 임페리얼(인치법) (야드파운드법 is the standard term); flow-run scopes 다운스트림/업스트림; Capture → 캡처 시점.
- Updates and crash reports: crash report → 충돌 보고서 (vs 비정상 종료 보고서 / 크래시 리포트); version sentences rephrased to avoid a particle after the number; Got it → 확인; Don't send / Never Send → 보내지 않음 / 보내지 않기 (deliberate).
- Background jobs: Blender's Korean 결합된 (Combined), 주변 폐색 (AO), 전달 (Transmission) inside "Bake {kind} textures"; 엔진 corrects Blender's typo 앤진; the 을(를) particle in the file-workspace promise.
- Job publication errors: link or junction → 링크나 정션; staging folder → 스테이징 폴더; Missing → 누락됨.

## Dutch (`nl`, `po/nl.po`)

- Apply → Toepassen: Blender's Dutch catalogue renders Apply as Toevoegen ("Add"), judged a mistranslation and not followed.
- "one Undo step" keeps Blender's label Maak Ongedaan in running text ("als één stap voor Maak Ongedaan"), which reads stiffly.
- Buttons use infinitives (Kopiëren, Genereren, Accepteren, Verwerpen) where Blender's Dutch uses imperatives (Kopieer, Genereer, Accepteer, Weggooien).
- Scene kept as "scene" (Blender's term) rather than scène.
- seat → licentie (alternative plek); collaborator → meedenker (alternatives adviseur, partnermodel); Project Brief → Projectbriefing.
- Director Mode → Regisseursmodus, Manual → Handmatig.
- Keychain → Sleutelhanger, Credential Manager → Referentiebeheer, keyring → sleutelbos; OS prompts use toestemming, Angelo's approvals goedkeuring.
- Global Undo and Sculpt Mode stay English (Blender's Dutch has no translation); read-back → uitlezing (coined); Showcase → Galerij.
- Dialog fields: Upstream/Downstream → Stroomopwaarts/Stroomafwaarts (flow-run scopes); Capture → Vastleggen.
- Updates and crash reports: Release notes → Releaseopmerkingen; Sorry about that → Het spijt ons.; {count} h ago → {count} u geleden; View Last Report Sent → Laatst verstuurde rapport bekijken (the longest label in its row).
- Background jobs: bake → bakken (Blender's Bak Actie); eight bake types Blender's Dutch lacks were supplied (Gecombineerd, Ambient occlusion, Normaal, UV, Emissie, Diffuus, Glanzend, Transmissie) and need a native check.
- Job publication errors: link or junction → koppeling of junction; staging folder → voorbereidingsmap; "gepubliceerd" reads as made public (see the shared note).

## Polish (`pl`, `po/pl.po`)

- Approve / Decline → Zatwierdź / Odmów (Blender already uses Odrzuć for Discard).
- Connect → Połącz (Blender's Podłącz comes from linking nodes).
- Project Brief → Brief projektu (native alternative Założenia projektu); seat → stanowisko (alternatives licencja, miejsce).
- Effort levels are masculine (Minimalny … Maks.) but shared with Media Studio quality options.
- read-back → odczyt kontrolny (coined).
- Key-store fills use "w: {name}" to avoid case and gender agreement; "Angelo on {device} can now use {org}." reworded for the fallback values.
- Messages with {count} but no plural forms are phrased "(liczba: {count})".
- Pop Out Chat → Otwórz czat w oknie; Dismiss → Ukryj; Stop Flow Runs → Zatrzymaj przepływ (shortened for width).
- "IP entity" rendered as element IP: meaning to confirm.
- Dialog fields: Imperial → Imperialny (Blender's Cesarski is a mistranslation); Upstream/Downstream → Poprzedzające/Następne; Connect with → Metoda połączenia; "0 is unlimited" → "0 = bez limitu".
- Updates and crash reports: {count} d ago → {count} d temu (unit symbol, since dni temu is wrong for 1); Angelo is up to date → Angelo jest aktualny; Read the release notes online → Informacje o wydaniu online (verb dropped for width).
- Background jobs: cache → pamięć podręczna with heavy plurals (Blender's term); "Nothing was published." → Nie zapisano wyniku.; "Anulowane przez agenta" next to the status word Anulowano.
- Job publication errors: link or junction → dowiązanie lub punkt połączenia; staging folder → folder tymczasowy; Missing → Brakuje.

## Portuguese (Portugal) (`pt`, `po/pt.po`)

- Spelling follows the 1990 agreement (atualizar, objeto, selecionar, ativar); Blender's pt catalogue mixes older spellings, so a few words differ by one letter from the menus beside them.
- Preview → pré-visualização; snapshot → instantâneo, restore point → ponto de restauro.
- Director Mode → Modo Realizador (pt-PT film sense); Project Brief → Ficha do projeto.
- Connect / connection → Ligar / ligação (Blender's own Connect is Conectar).
- Keychain → Porta-chaves, Keychain Access → Acesso a Porta-chaves: verify against macOS pt-PT.
- "your team" → "sua equipa" without article so it reads after prepositions ("em {org}").
- YOU (console speaker) → EU; add-on → adição (Blender's word; suplemento may be preferable).
- Run → Executar is 2.5× the English width.
- Dialog fields: Upstream/Downstream → A montante/A jusante; Scope → Âmbito; CLI listener "A escutar" (the microphone status says "A ouvir").
- Updates and crash reports: Angelo quit unexpectedly → O Angelo terminou inesperadamente; Download → Transferir; Check for Updates… → Procurar atualizações… (or Verificar?); Report on the website → Comunicar no site.
- Background jobs: bake → gerar e gravar (Blender's verb, heavy); time budget → Tempo máximo; kept frames restructured (fotogramas mantidos: {done} de {count}); five "see its Info log" sentences are long (Janela de informações).
- Job publication errors: link or junction → ligação ou junção; staging folder → pasta de preparação; Missing → Em falta; "publicado" reads as made public (see the shared note).

## Portuguese (Brazil) (`pt-BR`, `po/pt_BR.po`)

- Preview → pré-visualização (Blender's wording file says Previsão; Blender's own catalogue also uses pré-visualização); long in buttons.
- snapshot kept as "snapshot", restore point → ponto de restauração (alternative instantâneo).
- seat → licença ("Seat: yes" → "Licença: sim"; alternatives vaga, assento); Project Brief → Briefing do projeto.
- macOS names: Keychain → Chaves do macOS, Keychain Access → Acesso às Chaves; key store → cofre de chaves.
- read-back → releitura; Signed in → Login efetuado; Delete {path} → Apagar {path} (Blender's word).
- Console page names translated (Chaves, Pessoas): keep English if the console is English.
- Messages with {count} but no plural forms are phrased "alterações de arquivo revertidas: {count}".
- Subject (Scene Capture node) → Alvo (ambiguous; Conteúdo may be better).
- Dialog fields: Upstream/Downstream → A montante/A jusante; Scope → Escopo; "0 = sem limite".
- Updates and crash reports: Angelo quit unexpectedly → O Angelo foi encerrado inesperadamente; Skip this version → Pular esta versão (the catalogue renders Skipped as Ignorado); the tone of «Atualize-o.» and «Sentimos muito pelo ocorrido.».
- Background jobs: bake → gerar e gravar; Glossy → Polimento (Blender pt-BR's term, odd); Frame → Quadro; check "Substitui dados anteriores destas simulações, empacotados neste arquivo" is not softer than the English.
- Job publication errors: link or junction → link ou junção; staging folder → pasta de preparação; Missing → Faltando; "publicado" reads as made public (see the shared note).

## Russian (`ru`, `po/ru.po`)

- Coding Agent → Агент-программист (a coinage); collaborator → консультант, "Consult {model}" → Спросить {model}.
- read-back → контрольное считывание (invented, possibly heavy).
- Project Brief → Бриф проекта (alternative Описание проекта); flow → поток (also reads as thread/stream); seat → место.
- Keychain Access → Связка ключей (same as Keychain, as in macOS Russian); keyring kept in Latin letters.
- Fills ({name}, {store}, {org}, {device}) receive nominative phrases: templates put them after a colon, in parentheses or as «команда «{org}»».
- English singular/plural pairs without plural forms share one Russian text ("удалено … точек восстановления — {count}").
- Max / Xhigh → Максимальная / Очень высокая (feminine for глубина; wide, 3.7× and 2.6×); Enable Fast → Включить быстрый режим (2×).
- Discard → Отбросить (blender_wording's «Не сохранять» is the save-dialog sense).
- Checkbox «Я проверил(а) …» uses the gendered (а) form; Capture → «Создание снимков»; flow-run scopes «Последующие»/«Предшествующие»; Scope → «Охват»; Imperial → Blender's «Английская».
- Updates and crash reports: times «{count} мин / ч / дн. назад» avoid plural agreement; «Он не связан ни с вами, ни с вашей учётной записью.» must be exactly as strong as the English; Show less → Свернуть; Angelo is up to date → Версия Angelo актуальна.
- Background jobs: "Nothing was published." → «Результат не сохранён.»; "see its Info log" → «см. журнал в редакторе «Информация»»; agreement in «{count} уже запечённых кэша» (few); «{percent} %» with a no-break space.
- Job publication errors: link or junction → ссылка или точка соединения; staging folder → промежуточная папка; Missing → Отсутствуют.

## Swedish (`sv`, `po/sv.po`)

- Apply → Tillämpa (Blender's bare "Apply" is "Ansök", "apply for"); Open → Öppna (Blender has the adjective "Öppen").
- Discard preview → Kasta bort förhandsgranskning (Blender's "Kasta bort"; about 2× the English width).
- Editor Type → Redaktörstyp (Blender's own, odd); Quick Setup → Snabbinstallation (Blender writes two words).
- Director / Director Mode → Regissör / Regissörsläge; seat → licensplats; Project Brief → Projektbeskrivning.
- Read only (composer mode) → Endast läsning; elsewhere read-only is "skrivskyddad".
- face set → ytuppsättning, vertex group → vertexgrupp (Blender's catalogue mistranslates both).
- Keychain / Keychain Access / Credential Manager → nyckelring / Nyckelhanterare / Autentiseringshanteraren; the Linux keyring is also nyckelring.
- "Snapshot limit applied: removed {count} oldest restore point" (singular) wording to check.
- Dialog fields: Capture → Ta ögonblicksbild; Expand → Visa hela; Modified → Ändrad, singular like Tillagd/Borttagen (as group headings plural would read better).
- Updates and crash reports: Got it → Uppfattat (tone); Sorry about that → Vi ber om ursäkt. (too formal?); {count} h ago → för {count} tim sedan; Check Automatically / Check Now → Sök automatiskt / Sök nu.
- Background jobs: Cancelled → Avbruten and Interrupted → Oväntat avbruten; plural cache → cachar; Blender's Utsänd (Emit) and Överföring (Transmission); job card Open → Öppna in the Angelo context (Blender's default-context Öppen is an adjective).
- Job publication errors: link or junction → länk eller knutpunkt; staging folder → förberedelsemapp; "publicerades" reads as made public (see the shared note).

## Thai (`th`, `po/th.po`)

- Preferences → ความพึงใจ is Blender's Thai word but reads oddly in "Create/Save/Import … Preferences".
- Connect → เชื่อมต่อ (Blender's catalogue has the typo "เชื่อต่อ"); Node → โหนด (Blender says จุดต่อ; its Node Editor is ตัวแก้ไขโหนด).
- Manual / Manual Mode → แมนนวล / โหมดแมนนวล and Director → ผู้กำกับ (Blender's "Manual" is ด้วยตนเอง).
- Do anything → สั่งอะไรก็ได้ (empty chat placeholder); Finishing → กำลังดำเนินการ (shortened; check it still means finishing); Unfiled → ไม่มีโฟลเดอร์.
- Global Undo stays in Latin letters (Thai Blender does not translate that setting); Outliner → แผงกิ่ง (Blender's, obscure).
- Keychain / Keychain Access → พวงกุญแจ / การเข้าถึงพวงกุญแจ; Credential Manager → ตัวจัดการข้อมูลประจำตัว; Windows Credentials → ข้อมูลประจำตัว Windows: verify against the OS's Thai.
- Collaborator → ผู้ร่วมงาน (another model the agent consults).
- Docking: Return Chat to Dock → ยึดแชทกลับที่เดิม, Pop Out Chat → แยกหน้าต่างแชท, Dock Left → ยึดด้านซ้าย.
- Reasoning scale: Minimal น้อยที่สุด, Low ต่ำ, Medium ปานกลาง, High สูง, Extra high สูงพิเศษ, Max สูงสุด, Ultra อัลตรา: keep Max and Ultra distinct.
- Dialog fields: Capture → บันทึกเมื่อ ("capture when", read with its options ทุกคำขอ (ค่าเริ่มต้น) / ก่อนการแก้ไขแรกที่อนุมัติ (เลือกเปิดเอง)); flow-run scopes Upstream / Downstream / Node / All → ขั้นตอนก่อนหน้า / ขั้นตอนถัดไป / โหนดนี้ / ทั้งหมด; confirmations use ฉัน.
- Word breaks (U+200B, 7,111 of them, dictionary-segmented then hand-corrected): compound terms may still break at their parts (ภาพ|อ้างอิง, โฟลว์|การสร้าง, ตัวแก้ไข|โหนด), and short labels can wrap (แสดง|ตัวควบคุม, เลิกทำ|การคืนค่า); decide which should never break.
- Updates and crash reports: crash → ข้อขัดข้อง, so crash report → รายงานข้อขัดข้อง (Windows/macOS Thai also use รายงานข้อผิดพลาด); Angelo quit unexpectedly → Angelo ปิดโดยไม่คาดคิด; the privacy promise ending in เลย must be exactly as strong as "never"; {count} d ago breaks as วัน|ที่แล้ว, never วันที่.
- Background jobs: bake → อบ (Blender's Thai; artists often say เบค); bake types Blender lacks: รวม, การบดบังแสงโดยรอบ, ตำแหน่ง, ความหยาบ, สภาพแวดล้อม; {count} samples → {count} ตัวอย่าง (also Angelo's word for preview).
- Job publication errors: link or junction → ลิงก์หรือจุดเชื่อมต่อ; staging folder → โฟลเดอร์จัดเตรียม; Missing → ไม่พบ; "เผยแพร่" reads as made public (see the shared note).

## Turkish (`tr`, `po/tr.po`)

- agent → ajan (Microsoft uses aracı); seat → koltuk (alternatives lisans, kullanıcı yeri).
- AI Providers → Yapay Zeka Sağlayıcıları (about 2.1× the English, may clip in the Preferences sidebar); the editor-menu heading "AI" → Yapay Zeka (5.5×; "YZ" would fit).
- native → yerel, but local is also yerel ("Yerel CLI"); yerleşik would separate them.
- Generate → Oluştur (Blender's; Create is also Oluştur); generation → üretim.
- Outliner → Başlıklar and Global Undo → Genel İşlem Geçmişi follow Blender's own Turkish.
- Discard → At (terse); Pop Out Chat → Sohbeti ayır.
- Placeholder suffixes avoided with colon forms ("Sil: {path}") or carrier nouns ("{org} içinde").
- public notes / public brief → açık notlar / açık özet.
- Dialog fields: Imperial → Emperyal (Blender: İmparatorluk); flow-run scopes Upstream/Downstream → Önceki adımlar/Sonraki adımlar.
- Updates and crash reports: one possessive ending covers the privacy list («sahne, istem, sohbet, dosya ve anahtarlarınızı»), check it reads as "your" for each; Don't send → Gönderme (can read as a noun); build → sürüm, also the word for version.
- Background jobs: three bake types depart from Blender's mistranslations (Normal not Olağan, Yaygın not Yay, İletim not Aktarma), but Blender's catalogue still answers those first; Emit → Dışarı Ver as Blender shows it; render → işle vs the loanword render.
- Job publication errors: junction → bağlantı noktası (junction); staging folder → hazırlık klasörü; Missing → Eksik.

## Vietnamese (`vi`, `po/vi.po`)

- The sidebar tab "Flow" is drawn by Blender with its own catalogue first, so it shows Blender's physics term "Lưu Luồng/Lượng" (known limit: tab names have no message context).
- snapshot → bản chụp nhanh (chosen over "ảnh chụp", which reads as an image); check it reads as a restore point.
- Settings → Sắp đặt (Blender's word; most Vietnamese software says "Cài đặt"), e.g. Snapshot Settings → Sắp đặt bản chụp nhanh.
- seat → suất ("giấy phép" is an alternative); credits → tín dụng (can suggest a loan).
- Director / Manual → Đạo Diễn / Thủ Công, capitalised to match Blender's "Thủ Công".
- Do anything → "Bạn muốn làm gì?"; New Chat → Trò chuyện mới (shortened for width).
- Discard → Loại Bỏ (Blender's), so "Discard preview" → Loại bỏ bản xem trước.
- Generative AI → AI tạo sinh but Generate → Sinh tạo (Blender's): same words in opposite order.
- staged / stage → đã chuẩn bị / vùng dựng; native → gốc ("({ms:g} ms gốc)"): technical wording to check.
- Keychain → Chuỗi khóa; Keychain Access → Truy cập Chuỗi khóa; Credential Manager → Trình quản lý Thông tin xác thực: verify against macOS/Windows Vietnamese.
- Pop Out Chat → Tách trò chuyện ra; Return Chat to Dock → Gắn lại trò chuyện: docking wording, tight width.
- Dialog fields: flow-run scopes Upstream / Downstream → Các bước trước / Các bước sau; the checkbox confirmations speak as the person (Tôi xác nhận… / Tôi hiểu… / Tôi đã xem xét…); All → Toàn Bộ and Imperial → Hệ Đo Lường Anh Quốc are Blender's (wide).
- Welcome screen demo tiles: "Bevel and shade a model" and "Rig and pose a character" fill two lines and end in "…" (Bo tròn và tô bóng một mô hì…, Dựng khung rối và tạo tư thế ch…); shorter wording would show in full.
- Updates and crash reports: Release notes → Nhật ký phát hành (Blender's Help-menu term); What's New → Có gì mới (not Blender's literal Những Gì là Mới); crash report → báo cáo sự cố; Show less → Thu gọn.
- Background jobs: bake → Nướng (Blender's literal term); Normal → Pháp Tuyến; "Nothing was published." → Không có gì được công bố (check: it means not delivered to the chat, not made public); Ambient Occlusion → Blender's long Tính Hấp Thụ Quang Xạ Môi Trường.
- Job publication errors: "nothing was published" is now "không có kết quả nào được lưu" (nothing was saved), also in the three earlier job messages, replacing "không có gì được công bố", which read as made public; link or junction → liên kết hoặc junction; staging folder → thư mục chuẩn bị; Missing → Thiếu.

## Chinese (Simplified) (`zh`, `po/zh_HANS.po`)

- agent → 智能体 (alternatives 代理, or "Agent").
- provider → 服务商, "AI Providers" → "AI 服务商" (alternative 提供商).
- credits → 额度 (some products use 积分 or 点数).
- Conversations / Switch Conversation → 会话 / 切换会话, kept apart from chat (对话).
- OS permission prompts use 授权, the agent's approval uses 批准.
- Effort scale: Minimal 极低, Low 低, Medium 中, High 高, Extra high/Xhigh 超高, Max 最高, Ultra 极致.
- Approve for me → 替我批准; Do anything → 允许任何操作 (composer labels).
- Project Brief → 项目简报; recipe → 配方 (alternative 模板).
- "token" kept in Latin letters (词元 is the formal alternative).
- Where Blender's wording came from mesh or math contexts it was not followed: Close 关闭, Continue 继续, Add 添加, Usage 用量, project 项目.
- Unit scale → 缩放单位 copies Blender but reads oddly (单位缩放 may be better); Brief "Style" → 风格; 限制条件 avoids Blender's 约束.
- Updates and crash reports: Release notes → 发行说明; Report on the website → 在网站上报告问题 (报告 vs 反馈); Angelo {version} (is) available → 已推出 / 现已推出 (vs 可用); Show less → 收起.
- Background jobs: Blender's 合并结果 (Combined) and 糙度 (Roughness) inside 烘焙 {kind} 纹理; {count} 次采样; {count} 个已排队; "Nothing was published." → 未输出任何内容.
- Job publication errors: link or junction → 链接或联接点; staging folder → 暂存文件夹; Missing → 缺少.

## Chinese (Traditional) (`zh-TW`, `po/zh_HANT.po`)

- Object → 物體 (Blender zh_HANT); Taiwan users may expect 物件.
- agent → 代理, Coding Agent → 程式設計代理 (alternatives 程式代理, AI 代理).
- Undo restore → 撤銷還原 (Undo is 復原 elsewhere; 復原還原 would be awkward).
- Library → 素材庫 (Blender uses 藏庫 / 素材藏庫).
- credits → 點數 (alternative 額度); keyring → 金鑰圈, kept apart from the macOS 鑰匙圈.
- Approve for me → 替我核准; Do anything → 允許任何操作; From here → 從這裡 (very short node button).
- Effort scale: Minimal 最低, Low 低, Medium 中, High 高, Extra high/Xhigh 極高, Max 最大, Ultra 超高.
- Camera → 相機 (blender_wording), although Blender's catalogue mostly says 攝影機; polygon count → 多邊形數 (Blender: 多角形).
- Unit scale → 單位規模 copies Blender but is awkward (單位縮放 may be better); Node → 節點 (Blender's default context uses 結點); Capture → 擷取時機.
- Updates and crash reports: crash report → 當機報告 with Angelo quit unexpectedly → Angelo 意外結束; Release notes → 版本資訊; Always Send / Never Send / Don't send → 一律傳送 / 永不傳送 / 不要傳送; Show less → 顯示較少 (or 收合?).
- Background jobs: Bake → 烘焙 (Blender zh_HANT uses it 134 times against 20 for 烘培); 發光 for Emit (Blender's Emit is 發射); "Cancel puts them back" → 恢復原狀, avoiding 復原 (Undo).
- Job publication errors: link or junction → 連結或接合點; staging folder → 暫存資料夾; Missing → 缺少.
