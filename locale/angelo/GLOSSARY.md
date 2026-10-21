<!-- SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd. -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Angelo translation glossary

One rendering per term per language, used everywhere. The machine-readable form is
[`glossary.json`](glossary.json) (the validator warns when a catalogue entry uses a
glossary term without its approved rendering); the table below says what each term
means in Angelo so a translator or reviewer renders the concept, not the word.
Where Blender already has an established word (Scene, Preview, Cancel, Apply, Undo,
Library, Settings ...) use Blender's: `python3 scripts/angelo/i18n/cli.py hints <code>`.

**Never translated:** Angelo, Blender, Claude, ChatGPT, Codex, OpenAI, Anthropic, other
provider and model names, shortcuts (Ctrl+Alt+M, ⌃⌥M, Shift+M), file names and code.

## Terms

| Term | What it means in Angelo |
| --- | --- |
| Director | Angelo's default working mode: the 3D view plus the chat, nothing else on screen. A mode name, rendered the same in every menu, card and tip ("Director / Manual menu"). |
| Standard editor / Manual | The other mode: all of Blender's editors and toolbars. |
| agent | The AI that works in the scene on the person's behalf, through the chat. |
| chat | The conversation with the agent (the canvas, its messages and composer). |
| connection | A link to an AI account: a subscription sign-in (Claude, ChatGPT) or an API key. |
| provider | The company or service behind a model (Claude, OpenAI, Grok ...). |
| subscription | A paid plan on a provider's own site (Claude, ChatGPT), used through sign-in. |
| API key | A secret key for a provider's pay-per-use account. |
| model | One AI model of a provider. |
| reasoning effort | How much the model thinks before answering (Low / Medium / High). |
| Fast mode | The provider's paid lower-latency tier. |
| approval / approve / decline | The explicit permission the person gives before the agent changes the scene, runs a script or spends money. "Approve" and "Decline" are the buttons of an approval card. |
| preview | A result built on a copy of the scene that is not applied yet. |
| accept / discard | Keep a preview (applying it to the real scene) / throw it away. |
| apply | Make a reviewed change in the real scene. |
| snapshot | A restore point of the scene and of files the agent changed. "Restore snapshot" returns to it. |
| restore | Go back to a snapshot; the chat history is kept. |
| working copy / staged | The private copy of the scene where the agent builds before anything is applied. |
| read-back | What Angelo read back from Blender to confirm a change really happened. |
| Project Brief | The notes about the project (goal, style, units) the agent keeps in mind. |
| Media Studio | The editor for generating images, video and 3D models ("Generative AI" in the editor menu). |
| Generation Flow / flow | A node graph that chains generation steps; a *recipe* is a reusable flow. |
| recipe | A reusable, reviewed procedure (for example "Bevel and shade a model"). |
| team | An organisation's shared Angelo workspace. |
| seat | One person's paid place in a team. |
| admin / member | A team's administrators / its other people. |
| team library | Assets and IP the team shares. |
| console (team) | The team's web console, where admins manage people and keys. |
| credit / usage / quota / limit | What a provider charges or allows; "usage limit" is a provider's cap, with a reset time. |

## Words that mean something else in Blender

Blender's catalogue is asked first for every message, so a word both catalogues define shows Blender's wording.
These carry the message context `Angelo` wherever they are marked (`n_("Keys", "Angelo")`, `translation_context`
on a property), so Angelo's own meaning is shown: **Keys** (API keys, not keyframes), **Manual** (the mode, not the
handbook), **Prompt** (what you ask for, not a console cursor), **Close** (a dialog button, not a closed shape),
**Modified** (changed objects, not a date), **Assets** (Media Studio items, not financial assets), **Include**,
**Expand**, **Finishing**, **Transparent**. Add a word here, and its context, when a screenshot shows Blender's sense.

## Renderings per language

`glossary.json` holds each language's agreed renderings of the terms above (`cli.py check --warnings` flags a
message whose translation uses none of them; inflected languages trigger some warnings by design). Summary:

| Language | Director | Media Studio | snapshot | preview | Approve | Decline | seat |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Arabic (`ar`) | المخرج | Media Studio | لقطة | معاينة | وافق | ارفض | مقعد |
| German (`de`) | Regie | Media Studio | Snapshot | Vorschau | Genehmigen | Ablehnen | Lizenzplatz |
| Spanish (`es`) | Director | Media Studio | instantánea | previsualización | Aprobar | Rechazar | puesto |
| French (`fr`) | Réalisateur | Media Studio | instantané | aperçu | Approuver | Refuser | siège |
| Hindi (`hi`) | डायरेक्टर | Media Studio | स्नैपशॉट | पूर्वावलोकन | मंज़ूरी दें | अस्वीकार करें | सीट |
| Indonesian (`id`) | Sutradara | Media Studio | snapshot | pratinjau | Setujui | Tolak | kursi |
| Italian (`it`) | Regista | Media Studio | istantanea | anteprima | Approva | Rifiuta | postazione |
| Japanese (`ja`) | ディレクター | Media Studio | スナップショット | プレビュー | 承認 | 拒否 | シート |
| Korean (`ko`) | 디렉터 | Media Studio | 스냅샷 | 미리보기 | 승인 | 거부 | 시트 |
| Dutch (`nl`) | Regisseur | Media Studio | momentopname | voorbeeld | Goedkeuren | Weigeren | licentie |
| Polish (`pl`) | Reżyser | Media Studio | migawka | podgląd | Zatwierdź | Odmów | stanowisko |
| Portuguese (Portugal) (`pt`) | Realizador | Media Studio | instantâneo | pré-visualização | Aprovar | Recusar | lugar |
| Portuguese (Brazil) (`pt-BR`) | Diretor | Media Studio | snapshot | pré-visualização | Aprovar | Recusar | licença |
| Russian (`ru`) | Режиссёр | Media Studio | снимок | предпросмотр | Одобрить | Отклонить | место |
| Swedish (`sv`) | Regissör | Media Studio | ögonblicksbild | förhandsgranskning | Godkänn | Avböj | licensplats |
| Thai (`th`) | ผู้กำกับ | Media Studio | สแนปช็อต | ตัวอย่าง | อนุมัติ | ปฏิเสธ | ที่นั่ง |
| Turkish (`tr`) | Yönetmen | Media Studio | anlık görüntü | önizleme | Onayla | Reddet | koltuk |
| Vietnamese (`vi`) | Đạo Diễn | Media Studio | bản chụp nhanh | bản xem trước | Phê duyệt | Từ chối | suất |
| Chinese (Simplified) (`zh`) | 导演 | Media Studio | 快照 | 预览 | 批准 | 拒绝 | 席位 |
| Chinese (Traditional) (`zh-TW`) | 導演 | Media Studio | 快照 | 預覽 | 核准 | 拒絕 | 席位 |

The Flow sidebar tab is the one known exception: Blender draws tab names with its own catalogue and no
message context, so "Flow" shows Blender's word (in Vietnamese, its physics term).
