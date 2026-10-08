# 單字形遊戲測試

此規則用於判斷一個新繁中字形是否能從遊戲資源持續顯示。遊戲內RAM替換只驗證渲染因果；ROM驗收還要把同一字形替換到已定位的來源資源，再以新BIN/CUE開機重測。

1. 固定來源鏡像、模擬器核心、場景、影格和輸入，保存同設定未掛勾與掛勾基準。兩者畫面與必要memory dump必須一致。
2. 由實際啟用的圖層／繪圖命令、座標與裁切，選出落在文字框的候選圖塊。記錄格式、尺寸、原bytes與hash，並將圖塊解碼圖對照原畫面。
3. 在候選RAM圖塊持續多影格覆寫一個繁中字形，對比目標文字框像素。差異必須穩定出現在該框；只改memory、只變其他畫面，或圖案外觀像字都不通過。
4. 將原圖塊bytes在來源檔案中精確比對，確認唯一或明確列出全部相同位置，並保存來源檔SHA與相對offset。不能把執行時cache位址當成ROM位址。
5. 用遊戲專用adapter只改已證實的來源bytes，交給對應光碟工具建新鏡像；保存source/output hashes、變更LBA、EDC/ECC與音軌檢查結果。從新CUE啟動時不使用runtime覆寫，並確認目標文字框出現同一字形。

此測試只證明所驗字形及其已測文字框。它不證明完整字庫、任意中文字、變寬字體、文字記錄／控制碼回填或全遊戲翻譯已完成。

Vandal Hearts English v1.4 的已驗證profile位於工程 `scripts/build_rbg0_hud_glyph_trial.py`，輸出與readback證據位於 `translation-candidates/Vandal Hearts zh-Hant Glyph Trial/` 及 `analysis/emulator/artifacts/rbg0-hud-proof/`。該 HUD profile 只替換一個戰鬥 HUD 8×8 tile，不是通用字庫工具。另有專用英版對話字庫的 16×16 1bpp 實際 BIN/CUE 證據，範圍與證據見 [Vandal Hearts English v1.4 adapter](vandal-hearts-en-v1.4.md)；它不代表 HUD 或全遊戲翻譯完成。


## Vandal Hearts English v1.4 字庫安全界線

工程 `scripts/build_evdemo0_zh_hant.py` 的舊 v5 字形 hook 已被原版冷啟動與 v5 ROM 執行證據否決；預設建置必須拒絕。`--unsafe-legacy-v5-trial` 只供重現歷史失敗，不是交付選項。該版本把 94×94 位址公式誤當成可寫字庫，實際 `0.BIN` 在 row `0x30` 起會命中可執行程式碼；`0x0602B580`（0.BIN 檔案 offset `0x27580`）是執行期可變 scratch，冷啟動 trace 在 PC `0x060206898` 清零、PC `0x0602A990` 與 `0x060349A4` 寫入，不能放 hook 或字形資料。只讀 `0x060121F4`（檔案 offset `0xE1F4`）原始 helper 確認仍是原版指令；候選 hook 將它改為跳入該 scratch，v5 實際在 PC `0x0602B590` 遇到無效 opcode。

安全字庫 profile 必須證明每個來源位址的 ROM bytes 與執行時映像對應，並證明整段 storage 的 owner、寫入者、載入／搬移呼叫與場景生命期。算出可達 slot 數、看見零 bytes、單次字形讀取，或只靠另一版本／另一遊戲的 RAM 用法，均不構成安全配置證明。獨立字庫需同時有可定位的資源檔、可重現的 loader/目的地、renderer 指標更新與寬度路徑；缺少任一項時，不得建置可交付 ROM。
