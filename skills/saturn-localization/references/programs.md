# 可執行程式與資料契約

所有路徑均相對於技能目錄；執行時解析成絕對路徑。程式僅使用 Python 標準庫，`--help` 提供完整參數。輸出須使用不存在的檔案或目錄，避免覆蓋先前成果。

## 光碟目錄

`inspect_disc.py` 讀取 MODE1/2352 的 ISO9660 primary volume descriptor 和資料目錄，輸出檔名、LBA、大小及文字候選檔摘要。它不抽取全部遊戲檔案，也不解析遊戲劇本；多 session、其他扇區格式或特殊目錄需另選工具。

```bash
python3 scripts/inspect_disc.py --self-check
python3 scripts/inspect_disc.py '/path/Track 1.bin' --output '/work/disc-inventory.json'
```

來源完整 SHA256 可用 `sha256sum`，不要用檔名版本標籤代替雜湊。匯出的 LBA 是檔案定位資訊，不代表遊戲能接受重新排列。

## 固定文字區塊

`text_workflow.py extract/patch` 的輸入是已解出的遊戲檔案目录，不直接修改 BIN 扇區。它僅支援已確認的未壓縮固定容量区塊，每個区塊有 NUL 結尾並以零補滿。指標及其他檔案內容保持原值，溢出時拒絕寫入。

遊戲工程建立 `profile.json`，以下為合成示例，各位置與容量都必須來自該遊戲的分析：

```json
{
  "codec": "shift_jis",
  "files": {"DATA.BIN": "來源 DATA.BIN 的完整 SHA256"},
  "slots": [
    {"id": "scene01.line01", "file": "DATA.BIN", "offset": 128,
     "capacity": 64, "context": {"scene": "scene01", "speaker": "npc01"}}
  ]
}
```

`codec` 支援 ascii、latin-1、shift_jis、cp932、utf-8。它表示檔案實際採用的編碼，無法讓原遊戲自行支援中文。自訂編碼改用 `table` 欄位，其鍵為十六進位位元組、值為字元或控制占位符，例如 `{"a1":"A","a0":" ","fa":"⟦WAIT⟧"}`。實際設定須涵蓋所有使用字元；表必須可逆且位元組鍵不能互為前綴，也不能包含 NUL。其他編碼或結尾規則使用專用適配程式。

```bash
python3 scripts/text_workflow.py extract --root /work/original-files --profile /work/profile.json --output /work/records.jsonl
python3 scripts/text_workflow.py patch --root /work/original-files --profile /work/profile.json --records /work/translated.jsonl --output /work/modified-files
```

匯出記錄包含 `id`、`source`、`context`、`file`、`offset`、`capacity`、`raw_hex`。回填只增加 `target`，保留其他欄位；程式重新讀取來源，確認雜湊及所有原始欄位一致。它先檢查全部文字，再產生新目錄中的修改檔案。這個工具檢查位元組容量，不計算字型像素寬度。

專用適配程式也可輸出 JSONL，至少保留 `id` 和 `source`；將所需的定位與控制資料保留在各自的附加欄位中。它自己承擔其格式的解壓縮、重定位、重新編碼和無修改往返檢查。

## 翻譯工作與快取

`plan/merge` 可用於固定區塊及專用適配程式的 JSONL。輸出目標固定為 zh-Hant；namespace 必須符合 `<source>-zh-Hant:<model>:<glossary>`。例如 `ja-zh-Hant:luna-v1:glossary-v1`。相同 source、context、namespace 共用翻譯；修改來源或快取版本會產生不同工作 ID。程式保留原始 zh-Hant 快取（無額外 locale 欄位亦可由 namespace 識別），拒收韓文或 zh-Hans namespace，以及明確宣告其他目標語言的記錄。程式不呼叫任何模型或 API。

```bash
python3 scripts/text_workflow.py plan --records /work/records.jsonl --namespace 'ja-zh-Hant:model-v1:glossary-v1' --output /work/pending.jsonl
```

模型每次讀取一個有必要上下文的批次，只輸出 `id` 與 `target`，避免重複輸出源文或定位資訊。例如：

```json
{"id":"plan 產生的雜湊 ID","target":"你好⟦WAIT⟧"}
```

使用 `accept` 把回答和原始工作清單合併，驗證 ID 與控制占位符，產生含 source、context、namespace、target_language 的快取。占位符缺漏或順序改變會拒收，不自動補回。若控制碼節點和記錄邊界已確認，適配程式應提供控制碼間文字片段的翻譯工作，再由程式依原位置與參數重組。它允許只接收目前成功批次的回答，未知或重複 ID 會失敗。

```bash
python3 scripts/text_workflow.py accept --jobs /work/pending.jsonl --answers /work/batch-answers.jsonl --output /work/batch-cache.jsonl
```

把通過檢查的批次以 JSONL 合併為 `/work/translations.jsonl`，再執行：

```bash
python3 scripts/text_workflow.py merge --records /work/records.jsonl --translations /work/translations.jsonl --namespace 'ja-zh-Hant:model-v1:glossary-v1' --output /work/translated.jsonl
```

繼續翻譯時，`plan` 加上 `--translations /work/translations.jsonl`，只匯出尚未快取的工作。重複 ID 應先去除完全相同的記錄；若同一 ID 的譯文不同，保留經校對採用的版本。程式遇到重複 ID、來源變更、namespace 不符、非 zh-Hant 目標、缺譯或控制占位符缺漏/重排會失敗。`patch` 同樣要求翻譯記錄含 zh-Hant locale 或可識別的 zh-Hant namespace。來源 codec 仍以遊戲實際編碼為準；目標 locale 不會自動改變位元組編碼。`merge` 通過不代表字庫、容量或遊戲內顯示已通過。

```bash
python3 scripts/text_workflow.py self-check
```

此自檢使用 `/tmp` 中的合成檔案，驗證 zh-Hant 目標接受、韓文與簡中 namespace 拒收、控制占位符缺漏/重排拒收且不自動補、片段重組、往返編碼、上下文去重、快取版本、邊界、雜湊與來源保留；它不證明任一遊戲已適配。

## Guarded binary delta

`scripts/saturn_binary_delta.py` builds a compact, zlib-compressed patch for a fixed source/target pair. Its JSON header records source/target SHA-256 and sizes, changed byte spans, block size, and payload hash; the payload stores target bytes for changed blocks. It memory-maps inputs for bounded-memory comparison and supports target growth or truncation. `apply` requires the exact source hash, refuses an existing output path and verifies the reconstructed target hash before publishing it. Pass `--expected-source-sha256` and `--expected-target-sha256` to pin a known pair at both build and apply. It is a byte-file adapter: it does not inspect BIN/CUE tracks, ISO extents, or sector EDC/ECC. Keep `saturn_disc_patch.py` and `prepare_yabause_cue.py` for disc packaging and provenance.

```bash
python3 scripts/saturn_binary_delta.py self-check
python3 scripts/saturn_binary_delta.py build --source /work/original-track.bin --target /work/approved-track.bin --output /work/approved-track.sdelta
python3 scripts/saturn_binary_delta.py apply --source /work/original-track.bin --delta /work/approved-track.sdelta --output /work/rebuilt-track.bin
```

The target used for `build` must already be approved and independently identified. Preserve the emitted source/target hashes alongside any distributed delta; an exact reconstruction does not itself establish that the target's game behavior is complete or release-ready.
