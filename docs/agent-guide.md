# Agent 操作指南

這份指南描述何時讀 skill、如何把遊戲案例變成可驗證 adapter，以及如何報告完成範圍。始終遵守使用者提供的來源與授權邊界。

## 任務分流

1. **新遊戲或新版本：**先做唯讀盤點，記錄 CUE/軌道與輸入 hash，確認磁碟格式、檔案表和可用工具。未知記錄、壓縮、指標、控制碼、字型與 renderer 需有交叉證據；先將候選標為未驗證，不要由 NUL、ASCII 外觀或另一款遊戲的地址推斷語意。
2. **已有 adapter 的遊戲：**讀 `SKILL.md` 中對應版本 reference 與 adapter 的命令/help。只沿用相同 source SHA/profile 的通過規則；來源不同就重新確認受影響部分。
3. **文字翻譯：**抽取穩定 ID、上下文、source bytes 和控制節點。翻譯目標固定 zh-Hant，模型固定 `gpt-6-luna`；把純正文批次交模型，程式持有 offset/codec/control/容量。缺少模型時暫停需要語言推理的步驟，不替換模型。答案以 ID 合併，校驗未翻譯、重複、順序及控制參數。
4. **回填與鏡像：**用 Python adapter 處理 bytes。以當前候選 ISO 擷取目標完整模組，再對完整 source file SHA 做 guard；只改計畫的 span，驗證非目標 bytes 不變。先做無修改往返，再做候選 patch、新 BIN/CUE、ISO sector/EDC/ECC、各軌道與音訊驗收。
5. **遊戲內驗收：**用最終候選與正常硬體 profile、無文字/字型 runtime override。記錄唯一 run 目錄、模擬器 executable hash、CUE/BIN/各 track hash、state/cart/input hashes、機器設定和覆寫狀態。name cache 必須追到最後寫入和 PNC/map，再以可見像素驗證；CPU 讀取或 atlas bytes 單獨不足以證明畫面文字。

## 何時建立新 adapter

當同一款遊戲／版本會重複執行、格式規則已由 parser/runtime/byte comparison 確認，或回填有脆弱的容量與控制不變量時，把規則固化為帶版本名的 adapter 和自檢。純研究的一次性問題不需先造框架。Saturn 是硬體平台，不是共通文字格式：不要把別款遊戲 parser、字元表、地址、控制 regex、行寬或補丁 byte pattern 當預設。

Adapter 必須能明確回答：

- 來源 Track 1/Track 2 與完整被修改檔案的 SHA guard 是什麼？
- 哪些記錄邊界與控制節點已確認，哪些仍是候選？未知 bytes 怎樣原樣保存？
- Codec、空白、換行、NUL、參數、最大容量與中文字型 slot 如何驗證？
- 未修改 extract→parse→encode→write 是否逐 byte 等同來源？
- 最終輸出 BIN/CUE 是否為來源副本，資料軌、音軌、sector 和 helper/component 是否有獨立驗證？

## 字型與畫面證據

優先選擇真正適合目標像素尺寸的 bitmap/pixel font；灰階向量字下採樣與 threshold 僅供候選，不替代遊戲內可讀性驗收。字形尺寸、BBX、行序、advance、slot ID 和 renderer 取字公式需相互吻合。Font asset 更新保持 append-only，除非有已驗證的整體重排策略。

對 cache HUD，追蹤 producer、cache 寫入、同一影格後續寫入和最終 PNC/map entry，最後看目標像素。VDP2 host-side 合成或 VRAM 可見資料不等同 guest CPU 字型讀取。Sprite、FMV burned-in 字幕、原生文字和 UI raster 是不同路徑，按來源與 decoder 證據選工具。

## BIN/CUE 與 GUI 試玩

`scripts/saturn_disc_patch.py` 與 `scripts/saturn_binary_delta.py` 是 repo 中可重跑的 Python 工具；需先看 `--help`、self-check 和對應 reference。Disc patch 使用目前源鏡像與 extract manifest，拒絕 source hash 不同或覆寫輸出。差異包 applier 只能用在其 manifest 指定的來源 hash 上；它重建既有成品，不重跑提取、分析或翻譯。

本 repo 不含 GUI 模擬器、ROM、CUE、state、cartridge sidecar 或 headless runner。只有使用者工程內確實存在已驗證 play launcher 和候選鏡像時，才依該工程 README 執行其快速 state 與 `--cold` 模式。manifest 比較不代表實際 combined BIN bytes 已重算 hash；先以專用 disc verifier 驗證實際資料，再執行。視窗成功開啟是初始化證據；螢幕像素、鍵盤輸入與音訊必須分開觀察。固定 state 的像素比對只驗證該有限影格，不能代替冷開機或通關。

## 報告狀態

明確區分「候選」「可提取」「可無損回填」「可顯示中文字」「已在遊戲中驗收」。逐項報告實際測試場景、可見內容、無 override 與 provenance。靜態覆蓋不等於 runtime PASS；片段影片 PASS 不等於全片或全遊戲完成。未完整通關或未測的選單、字幕與自然解鎖路徑要直接列出。

## 閱讀路由

- Saturn 通用流程：讀 `skills/saturn-localization/SKILL.md`。
- 光碟格式、程式及 emulator provenance：依任務讀該 skill 的 `references/disc-patching.md`、`references/programs.md`、`references/emulator-provenance.md`。
- glyph/capacity 或 runtime UI：讀 `references/glyph-trial.md` 與對應遊戲 adapter。
- Vandal Hearts English v1.4：讀 `references/vandal-hearts-en-v1.4.md` 和 `references/vh-hud-experimental.md`。`vh-expanded-ui-patch.md`、`vh-v25-candidate-rebuild.md` 依其檔內標示是案例外部工程 recipe；其中提到的 scripts/manifests/artifacts 不隨 npx skill 發佈。先確認外部工程檔案存在與 source version 一致，不要把 reference 範例路徑當 repo 內置工具。
