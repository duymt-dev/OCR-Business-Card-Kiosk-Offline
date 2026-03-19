(() => {
    const $ = (sel) => document.querySelector(sel);
    const getVal = (sel) => (($(sel) || {}).value || "").trim();
    const setVal = (sel, v) => { const el = $(sel); if (el) el.value = v || ""; };

    const logEl = $("#log");
    const state = {
        audio: true,
        faceDataUrl: null,
        sourceImages: [],
        serverQRUrl: null,
        qrScanner: null,

        // プレゼント検知 (Cam2 → Python)
        presenceSending: false,
        // Pi 5: CPU負荷を抑えるため ~1fps
        presenceFps: 1,
        // 顔検出エンドポイント (顔の bbox)
        presenceEndpoint: "/api/presence/frame",
        faceEndpoint: "/api/face/frame",
        // ボックス描画済みのフレームを返すエンドポイント (drawBoxesOnStream=true の場合に使用)
        presenceEndpointWithBoxes: "/api/face/frame_with_boxes",
        drawBoxesOnStream: false,  // true = サーバーからのフレームに直接ボックスを描画
        /** 顔を連続して検出してから自動撮影するまでの時間 (ms) — この変数を変更して時間を調整してください。 */
        faceAutoCaptureMs: 0,
        faceSeenSinceMs: 0,
        faceAutoCaptured: false,
        faceCaptureReadyAtMs: 0,

        // スキャンライフサイクル
        scanLockedCam1: false,
        scanLockedCam2: false,
        isSubmitting: false,

        // Cam2 ズーム
        video2ZoomRunning: false,
        video2ZoomHandle: null,
        video2ZoomFactor: 1.3,    // 2.5x zoom

        // 生バッファ / 構造化データ
        lastQRRaw: null,
        lastBCardText: null,
        lastBCardFields: null,
        bcardImageDataUrl: null,
        registrationId: null,

        // OCR & 名刺自動撮影
        allowPresence: true,        // 初めからオンにしてユーザーを歓迎する
        emptyGapCount: 0,           // リセット用に空のフレームをカウント
        emptyGapRequired: 1,       // リセットするために連続した空のフレーム（10フレーム）が必要
        cardAutoSending: false,     // 名刺自動検出フレームを送信中
        cardAutoDone: false,        // 安定した名刺を1つ受信済み
        cardAutoFps: 3,             // リサイズ済みの Pi にとって最適なレベルは 6fps

        // エンドポイント
        payloadEndpoint: "/api/presence/payload",
        bcardOCREndpoint: "/api/ocr/bcard",
        bcardOCRAsyncStartEndpoint: "/api/ocr/bcard_async/start",
        bcardOCRAsyncQuickStartEndpoint: "/api/ocr/bcard_async/quick",
        bcardOCRAsyncStatusBase: "/api/ocr/bcard_async/status",
        cardAutoEndpoint: "/api/card/frame",
        autoCyclePhase: "IDLE", // IDLE, CARD, FACE, SUBMITTING
        presenceGreetCooldownMs: 8000,      // 挨拶の間のクールダウン 2s
        presenceLastGreetTs: 0,
        presenceLastHadPerson: false,
        presenceLastFaceCount: 0,
        presenceFaceIncreaseStreak: 0,
        presenceStableIncreaseFrames: 1,
        suppressGreetingDuringCompletion: false,
        audioQueue: [],             // 音声再生キュー
        isAudioPlaying: false,       // 再生状態
        cardRetryCount: 0,           // カード読み取り失敗回数
        handlingInvalidQr: false,
        welcomeIdleTimeoutMs: 30000,
        welcomeIdleTimer: null,
        welcomeEyesVisible: true,
        ocrTaskId: null,
        ocrStatus: "idle", // idle, processing, done, error
        ocrPollHandle: null,
        sessionFinalizeTriggered: false,
        cardRetryRequested: false,
        faceGuideAudioTimer: null,
        faceRetryCount: 0,           // 顔撮影リトライ回数 (最大 3)
        faceRetryMaxCount: 15,        // リトライ制限
        faceRetryTimer: null,        // 音声後の確認待ちタイマー 2s
        thankYouResetTimer: null,    // 感謝バッジ表示後のリセットタイマー
        _resetRestartTimer: null,    // resetAll 後のカメラ再起動タイマー（即座のリセットをキャンセルするため）
        _presenceGeneration: 0,      // 二重の presence ループ防止用の世代カウンター
        speculativeTextThreshold: 20, // リトライケースで「情報あり」とみなす最小文字数
        // ロボットアバターロジック
        avatar: {
            target: { x: 0.5, y: 0.5 },
            cur: { x: 0.5, y: 0.5 },
            rafId: null,
            blinkTimer: null,
            metrics: { eyeW: 0, eyeH: 0, radiusX: 0, radiusY: 0 }
        }
    };
    function computeAvatarMetrics() {
        const eyeL = document.querySelector(".welcome-eye.left");
        if (!eyeL) return;
        const r = eyeL.getBoundingClientRect();
        state.avatar.metrics.eyeW = r.width;
        state.avatar.metrics.eyeH = r.height;
        state.avatar.metrics.radiusX = r.width * 0.30;
        state.avatar.metrics.radiusY = r.height * 0.26;
    }

    function applyPupil(p, offX, offY) {
        if (!p) return;
        p.style.transform = `translate(calc(-50% + ${offX}px), calc(-50% + ${offY}px))`;
    }

    function tickAvatar() {
        if (!state.welcomeEyesVisible) {
            state.avatar.rafId = requestAnimationFrame(tickAvatar);
            return;
        }
        const a = state.avatar;
        const lerp = (a, b, t) => a + (b - a) * t;
        const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

        a.cur.x = lerp(a.cur.x, a.target.x, 0.12);
        a.cur.y = lerp(a.cur.y, a.target.y, 0.12);

        const dx = clamp((a.cur.x - 0.5) * 2, -1, 1) * a.metrics.radiusX;
        const dy = clamp((a.cur.y - 0.5) * 2, -1, 1) * a.metrics.radiusY;

        applyPupil(document.querySelector(".welcome-eye.left .welcome-pupil"), -dx, dy); // Mirrored
        applyPupil(document.querySelector(".welcome-eye.right .welcome-pupil"), -dx, dy);

        a.rafId = requestAnimationFrame(tickAvatar);
    }

    function randomBlink() {
        const overlay = $("#welcome-eyes-overlay");
        if (overlay && state.welcomeEyesVisible) {
            overlay.classList.add("blink");
            setTimeout(() => overlay.classList.remove("blink"), 120);
        }
        state.avatar.blinkTimer = setTimeout(randomBlink, 1800 + Math.random() * 2600);
    }


    state.cam1Facing = "user"; // または "user"
    state.cam2Facing = "user"; // または "user"

    const ui = {
        btnRegister: document.querySelector("#btn-register"),
        btnPrintQR: document.querySelector("#btn-print-qr"),
        btnPrint3D: document.querySelector("#btn-print-3d"),
    };

    function setDisabled(el, disabled) {
        if (!el) return;
        if (disabled) {
            el.setAttribute('disabled', '');
            el.classList.add('is-disabled');
        } else {
            el.removeAttribute('disabled');
            el.classList.remove('is-disabled');
        }
    }

    function hasCccdData() {
        const name = getVal("#fullName");
        const id = getVal("#idNumber");
        return !!(name && id);
    }

    function hasBcardData() {
        const f = state.lastBCardFields || collectBCardFields();
        if (!f) return false;
        const vals = [
            f.full_name || f.name,
            f.email,
            f.phone || f.tel,
            f.title || f.position || f.role,
            f.company || f.org,
            f.address,
        ];
        return vals.some(v => (v || "").trim().length > 0);
    }

    function updateActionButtons() {
        const readyForRegister = hasCccdData() || hasBcardData();
        setDisabled(ui.btnRegister, !readyForRegister || state.isSubmitting);
        setDisabled(ui.btnPrintQR, !state.serverQRUrl);
        setDisabled(ui.btnPrint3D, !state.faceDataUrl);
    }

    // オーバーレイ電源用の cam1 状態を出力
    window.cam1Started = false;

    const sndThank = new Audio("/static/sound/camon.mp3"); sndThank.preload = "auto";
    const sndFaceGuide = new Audio("/static/sound/huongdanchupface.mp3"); sndFaceGuide.preload = "auto";
    const sndGreet = new Audio("/static/sound/chaobanjp.mp3"); sndGreet.preload = "auto";
    const sndCardQrGuide = new Audio("/static/sound/huongdanchupcardvaqr.mp3"); sndCardQrGuide.preload = "auto";
    const sndRegisterDone = new Audio("/static/sound/camonjp.mp3"); sndRegisterDone.preload = "auto";
    const sndQrInvalid = new Audio("/static/sound/qrkohople.mp3"); sndQrInvalid.preload = "auto";
    const sndCardUnread = new Audio("/static/sound/ChuadocdcDanhthiep.mp3"); sndCardUnread.preload = "auto";
    const sndFaceNotReady = new Audio("/static/sound/chuaromat.mp3"); sndFaceNotReady.preload = "auto";

    function log(msg) {
        const t = new Date().toLocaleTimeString();
        if (logEl) logEl.innerText = `[${t}] ${msg}\n` + logEl.innerText;
        else console.log(msg);
    }

    function showWelcomeEyes(show) {
        const overlay = $("#welcome-eyes-overlay");
        if (!overlay) return;
        overlay.classList.toggle("is-hidden", !show);
        state.welcomeEyesVisible = !!show;
        if (show) {
            computeAvatarMetrics();
            overlay.classList.remove("mood-happy", "mood-sad");
            overlay.classList.add("mood-neutral");
        }
    }

    function clearWelcomeIdleTimer() {
        if (!state.welcomeIdleTimer) return;
        clearTimeout(state.welcomeIdleTimer);
        state.welcomeIdleTimer = null;
    }

    function shouldResetToWelcome() {
        return (
            state.autoCyclePhase !== "IDLE" ||
            !!state.faceDataUrl ||
            !!state.serverQRUrl ||
            !!state.lastQRRaw ||
            !!state.lastBCardText ||
            hasCccdData() ||
            hasBcardData()
        );
    }

    function scheduleWelcomeIdleReturn() {
        if (state.welcomeEyesVisible || state.welcomeIdleTimer) return;
        state.welcomeIdleTimer = setTimeout(async () => {
            state.welcomeIdleTimer = null;
            if (state.presenceLastHadPerson) return;

            // 問題 3: カード認識リセット - Cam1 トレイにカードが残っている場合はリセットをブロック
            if (state.cardAutoDone || state.autoCyclePhase === "REMOVING") {
                log("待ち時間が終了しましたが、トレイにカードが残っています。カードを取り出すよう求めています。");
                state.autoCyclePhase = "REMOVING";
                state.emptyGapCount = 0;
                showRemoveCardOverlay(true, true);
                return;
            }

            if (shouldResetToWelcome()) {
                await resetAll(true);
            }
            showWelcomeEyes(true);
            log("60秒間ユーザーがいません。挨拶画面（ソフトリセット）に戻ります。");
        }, state.welcomeIdleTimeoutMs);
    }

    // function speak(text) {
    //     if (!state.audio) return;
    //     try {
    //         const u = new SpeechSynthesisUtterance(text);
    //         u.lang = "vi-VN";
    //         speechSynthesis.cancel();
    //         speechSynthesis.speak(u);
    //     } catch { }
    // }

    /**
     * 音声キュー: 挨拶や案内が重ならないようにします。
     */
    function playQueuedAudio(audio) {
        if (!state.audio || !audio) return;

        state.audioQueue.push(audio);
        if (!state.isAudioPlaying) {
            processAudioQueue();
        }
    }

    async function processAudioQueue() {
        if (state.audioQueue.length === 0) {
            state.isAudioPlaying = false;
            return;
        }

        state.isAudioPlaying = true;
        const audio = state.audioQueue.shift();

        try {
            audio.currentTime = 0;
            await audio.play();

            // Đợi cho đến khi âm thanh kết thúc
            return new Promise((resolve) => {
                const onEnded = () => {
                    audio.removeEventListener("ended", onEnded);
                    // 2つの音声の間に少し休憩を入れる (0.3s)
                    setTimeout(() => {
                        processAudioQueue();
                        resolve();
                    }, 300);
                };
                audio.addEventListener("ended", onEnded);
            });
        } catch (e) {
            console.error("音声キュー再生エラー:", e);
            state.isAudioPlaying = false;
            processAudioQueue();
        }
    }

    function playAudioAndWait(audio, forcePlay = false) {
        return new Promise((resolve) => {
            // 選択肢 1「強者が勝つ」: 現在再生中の音声（挨拶や案内など）をすべてクリアし、
            // この重要なコマンドに完全に道を譲ります。
            if (forcePlay || audio) interruptAndClearAudioQueue();

            if ((!state.audio && !forcePlay) || !audio) {
                if (!state.audio && forcePlay) {
                    log("緊急アラート: 音声オフ状態をバイパスします。");
                }
                if (!audio) {
                    log("再生する音声が見つかりません。");
                }
                resolve();
                return;
            }
            let timeoutId = null;
            const done = () => {
                if (timeoutId) {
                    clearTimeout(timeoutId);
                    timeoutId = null;
                }
                audio.removeEventListener("ended", done);
                audio.removeEventListener("error", done);
                resolve();
            };
            audio.addEventListener("ended", done);
            audio.addEventListener("error", done);
            // フォールバック: ブラウザで ended/error が発生しない場合のフリーズを防止
            timeoutId = setTimeout(done, 12000);
            try {
                audio.currentTime = 0;
                const p = audio.play();
                if (p && typeof p.catch === "function") {
                    p.catch((err) => {
                        log("QRエラー音を再生できません: " + (err?.message || err));
                        done();
                    });
                }
            } catch {
                done();
            }
        });
    }

    function interruptAndClearAudioQueue() {
        state.audioQueue = [];
        state.isAudioPlaying = false;
        const list = [
            sndThank,
            sndFaceGuide,
            sndGreet,
            sndCardQrGuide,
            sndRegisterDone,
            sndQrInvalid,
            sndCardUnread,
            sndFaceNotReady,
        ];
        for (const a of list) {
            try {
                a.pause();
                a.currentTime = 0;
            } catch { }
        }
    }

    async function startFaceGuidanceAndArmCapture() {
        state.autoCyclePhase = "FACE";
        state.presenceFps = 5;
        state.allowPresence = true;
        state.faceAutoCaptured = false;
        state.faceSeenSinceMs = 0;
        state.faceCaptureReadyAtMs = Date.now();
        state.suppressGreetingDuringCompletion = true;

        interruptAndClearAudioQueue();
        if (!state.presenceSending) {
            startPresenceStream();
        }
        // 音声遅延 3s — カードエラーの場合に OCR が失敗するのに十分な時間
        if (state.faceGuideAudioTimer) clearTimeout(state.faceGuideAudioTimer);
        state.faceGuideAudioTimer = setTimeout(async () => {
            state.faceGuideAudioTimer = null;
            if (state.autoCyclePhase !== "FACE") return; // すでにキャンセル済み
            await playAudioAndWait(sndFaceGuide, true);
            if (state.autoCyclePhase === "FACE") {

                // N = 2s: 撮影許可から 1s 後、さらに 2s 待っても撮影できない場合に通知
                if (state.faceRetryTimer) clearTimeout(state.faceRetryTimer);
                state.faceRetryTimer = setTimeout(async () => {
                    state.faceRetryTimer = null;
                    if (state.autoCyclePhase === "FACE" && !state.faceAutoCaptured) {
                        log(`顔写真を撮影できませんでした (リトライ ${state.faceRetryCount + 1})...`);
                        // 問題 1: リマインド音声は最大 3 回までに制限
                        if (state.faceRetryCount < state.faceRetryMaxCount) {
                            await playAudioAndWait(sndFaceNotReady, true);
                        }
                        state.faceRetryCount++;

                        // 常に繰り返します。リトライ回数による resetAll は行いません。
                        state.faceSeenSinceMs = 0;
                        state.faceCaptureReadyAtMs = Date.now() + 500;
                        startFaceRetryTimer();
                    }
                }, 2000); // 1s (ready) + 2s (N) = 3s
            }
        }, 1000);
    }

    function startFaceRetryTimer() {
        if (state.faceRetryTimer) clearTimeout(state.faceRetryTimer);
        state.faceRetryTimer = setTimeout(async () => {
            state.faceRetryTimer = null;
            if (state.autoCyclePhase === "FACE" && !state.faceAutoCaptured) {
                // 最大リトライ回数に達した場合は、中止してリセット
                if (state.faceRetryCount >= state.faceRetryMaxCount) {
                    log("許可された試行回数を超えました。待機画面に戻ります。");
                    resetAll(true);
                    return;
                }

                log(`まだ顔写真を撮影できません (リトライ ${state.faceRetryCount + 1})...`);
                // 問題 1: リマインド音声は最大 3 回までに制限
                if (state.faceRetryCount < 3) {
                    await playAudioAndWait(sndFaceNotReady, true);
                }
                state.faceRetryCount++;

                state.faceSeenSinceMs = 0;
                startFaceRetryTimer();
            }
        }, 5000); // 撮影できない場合、5秒ごとにリマインド
    }

    // ====== フォームエリアのヘルパー ======
    function getInfoSection() {
        const full = $("#fullName");
        if (full) {
            const form = full.closest(".form");
            const sec = form ? form.closest("section.panel") : null;
            return sec || document.querySelector(".right-pane") || document.body;
        }
        return document.querySelector(".right-pane") || document.body;
    }

    function ensureBCardPane() {
        let pane = document.getElementById("bcard-pane");
        if (pane) return pane;

        const infoSec = getInfoSection();
        if (!infoSec) return null;

        pane = document.createElement("div");
        pane.id = "bcard-pane";
        pane.className = "form form-12";
        pane.style.display = "none";
        pane.innerHTML = `
      <label class="span-3"><input id="bcardFullName" type="text" placeholder="名刺の氏名"></label>
      <label class="span-3"><input id="bcardTitle" type="text" placeholder="役職（例：営業部長）"></label>
      <label class="span-3"><input id="bcardEmail" type="text" placeholder="E-mail"></label>
      <label class="span-3"><input id="bcardPhone" type="text" placeholder="電話番号"></label>
      <label class="span-3"><input id="bcardCompany" type="text" placeholder="会社名"></label>
      <label class="span-3"><input id="bcardAddress" type="text" placeholder="会社住所"></label>
      <div id="bcard-raw" class="mono small muted span-3"></div>
      `;
        const cccdForm = infoSec.querySelector(".form");
        if (cccdForm && cccdForm.parentNode === infoSec) {
            cccdForm.after(pane);
        } else {
            infoSec.appendChild(pane);
        }
        return pane;
    }

    function toggleCCCDForm(show) {
        const infoSec = getInfoSection();
        if (!infoSec) return;
        const cccdForm = Array.from(infoSec.querySelectorAll(".form"))
            .find(f => f.querySelector("#fullName"));
        if (cccdForm) cccdForm.style.display = show ? "" : "none";
    }

    function toggleBCardPane(show) {
        const pane = ensureBCardPane();
        if (pane) pane.style.display = "none"; // ユーザーの要求により常に非表示
    }

    function fillBCardPane(fields = {}, rawText = "") {
        setVal("#bcardFullName", fields.full_name || fields.name || "");
        setVal("#bcardEmail", fields.email || "");
        setVal("#bcardPhone", fields.phone || fields.tel || "");
        setVal("#bcardTitle", fields.title || fields.position || fields.role || "");
        setVal("#bcardCompany", fields.company || fields.org || "");
        setVal("#bcardAddress", fields.address || "");
        setVal("#bcardOtherinfo", fields.other_info || fields.info || rawText || "");

        const raw = $("#bcard-raw");
        if (raw) raw.textContent = (rawText || "").trim();
    }

    // ====== UI: カード取り出し要求オーバーレイ ======
    function ensureRemoveCardOverlay() {
        let el = document.getElementById("remove-card-overlay");
        if (el) return el;

        const infoSec = document.querySelector(".right-pane") || getInfoSection() || document.body;
        if (infoSec && getComputedStyle(infoSec).position === "static") {
            infoSec.style.position = "relative";
        }
        el = document.createElement("div");
        el.id = "remove-card-overlay";
        Object.assign(el.style, {
            position: "absolute",
            inset: "0",
            background: "rgba(0,0,0,0.75)",
            display: "none",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            zIndex: "1001",
            color: "#fff",
            textAlign: "center",
            backdropFilter: "blur(4px)",
        });

        el.innerHTML = `
            <div style="font-size:24px; font-weight:bold; color:#ff9800; margin-bottom:10px;">
                ご利用ありがとうございました！
            </div>
            <div id="remove-card-text" style="font-size:18px; line-height:1.5;">
                <b>名刺をお忘れなくお持ち帰りください。</b>
            </div>
        `;
        infoSec.appendChild(el);
        return el;
    }

    function showRemoveCardOverlay(show, hasCard = true) {
        const el = ensureRemoveCardOverlay();
        el.style.display = show ? "flex" : "none";
        if (show) {
            const txt = document.getElementById("remove-card-text");
            if (txt) txt.style.display = hasCard ? "block" : "none";
        }
    }

    const btnAudio = $("#btn-audio-toggle");
    if (btnAudio) btnAudio.addEventListener("click", () => {
        state.audio = !state.audio;
        btnAudio.textContent = state.audio ? "🔊 音声オフ" : "🔇 音声オン";
    });

    // ====== Camera defaults + localStorage key ======
    const CAM1_PREF_KEY = "kiosk_cam1_deviceId";
    const CAM2_PREF_KEY = "kiosk_cam2_deviceId";

    function getSavedCam(key) {
        try { return localStorage.getItem(key) || ""; }
        catch { return ""; }
    }
    function saveCam(key, value) {
        try { localStorage.setItem(key, value || ""); }
        catch { }
    }
 
    // ====== カメラリスト (Cam1 & Cam2) ======
    async function populateCameras() {
        try {
            if (!window.QrScanner) throw new Error("qr-scanner がロードされていません。");
            const cams = await window.QrScanner.listCameras(true);

            const cam1Sel = $("#cam1-select");
            const cam2Sel = $("#cam2-select");
            if (cam1Sel) cam1Sel.innerHTML = "";
            if (cam2Sel) cam2Sel.innerHTML = "";

            const savedCam1 = getSavedCam(CAM1_PREF_KEY);
            const savedCam2 = getSavedCam(CAM2_PREF_KEY);

            let cam1ByLabel = null;
            let cam2ByLabel = null;

            cams.forEach((c, idx) => {
                const label = c.label || c.id;

                if (cam1Sel) {
                    const o1 = document.createElement("option");
                    o1.value = c.id;
                    o1.textContent = label;
                    cam1Sel.appendChild(o1);
                }

                if (cam2Sel) {
                    const o2 = document.createElement("option");
                    o2.value = c.id;
                    o2.textContent = label;
                    cam2Sel.appendChild(o2);
                }

                // ヒューリスティック: QR/名刺カメラは "USB 2.0 Camera" を優先
                if (!cam1ByLabel && /USB\s*2\.0\s*Camera/i.test(label)) {
                    cam1ByLabel = c.id;
                }
                // ヒューリスティック: 顔カメラ — 常にラベル "HD Webcam C525" を優先
                // Raspberry Pi の DeviceId は起動ごとに変わるため、デフォルトとして savedCam2 は使用しない
                if (!cam2ByLabel && /HD\s*Webcam\s*C525/i.test(label)) {
                    cam2ByLabel = c.id;
                }
            });

            // Cam1: label match → saved → first camera
            let defaultCam1Id = cam1ByLabel || savedCam1 || (cams[0] ? cams[0].id : "");

            // Cam2: 常にラベル "HD Webcam C525" を優先 (再起動後に deviceId が変わるため)
            // ラベルの一致が見つからない場合にのみ savedCam2 を使用
            let defaultCam2Id = cam2ByLabel || savedCam2 || (cams[1] ? cams[1].id : defaultCam1Id);

            if (cam1Sel && defaultCam1Id) {
                cam1Sel.value = defaultCam1Id;
                saveCam(CAM1_PREF_KEY, defaultCam1Id);
                if (!cam1Sel.dataset.bound) {
                    cam1Sel.addEventListener("change", () => {
                        saveCam(CAM1_PREF_KEY, cam1Sel.value || "");
                    });
                    cam1Sel.dataset.bound = "1";
                }
            }

            if (cam2Sel && defaultCam2Id) {
                cam2Sel.value = defaultCam2Id;
                // ラベル経由で見つかった最新の deviceId で localStorage を更新
                saveCam(CAM2_PREF_KEY, defaultCam2Id);
                if (!cam2Sel.dataset.bound) {
                    cam2Sel.addEventListener("change", () => {
                        saveCam(CAM2_PREF_KEY, cam2Sel.value || "");
                    });
                    cam2Sel.dataset.bound = "1";
                }
            }

            log(`${cams.length} 台のカメラが見つかりました。`);
        } catch (e) {
            log("カメラリストを取得できません: " + e.message);
        }
    }

    function uiSetScanLockedCam1(locked) {
        state.scanLockedCam1 = locked;
        const badge = $("#cam1-paused");
        if (badge) badge.hidden = !locked;
        const el = $("#scan-bcard"); if (el) el.disabled = locked;
    }
    function uiSetScanLockedCam2(locked) {
        state.scanLockedCam2 = locked;
        const badge = $("#cam2-paused");
        if (badge) badge.hidden = !locked;
        const el = $("#scan-qr"); if (el) el.disabled = locked;
    }

    async function lockScanCam1(reason = "") {
        if (state.scanLockedCam1) return;
        uiSetScanLockedCam1(true);
        log("Cam 1 のスキャンをロックしました" + (reason ? `: ${reason}` : "") + ".");
    }
    async function unlockScanCam1() {
        uiSetScanLockedCam1(false);
        log("Cam 1 のスキャンを再開しました。");
        // オートサイクルが完了していない場合は、自動検出を自動的に再実行
        if (!state.cardAutoDone) {
            startAutoCardFromCam1();
        }
    }

    async function lockScanCam2(reason = "") {
        if (state.scanLockedCam2) return;
        uiSetScanLockedCam2(true);
        // qrScanner.stop() は使用しません。カメラトラックがすべてオフになり、自動顔写真撮影ができなくなるためです。
        // ロックロジックは state.scanLockedCam2 フラグを使用して QrScanner のコールバックで処理されています。
        log("Cam 2 のスキャンをロックしました" + (reason ? `: ${reason}` : "") + ".");
    }

    async function unlockScanCam2() {
        uiSetScanLockedCam2(false);
        try {
            if (state.qrScanner) await state.qrScanner.start();
            else await startCam2();
            log("Cam 2 のスキャンを再開しました。");
        } catch (e) { log("Camera 2 (QR) を再起動できません: " + e.message); }
    }

    function collectFormData() {
        return {
            fullName: getVal("#fullName"),
            idNumber: getVal("#idNumber"),
            dob: getVal("#dob"),
            issued: getVal("#issued"),
            address: getVal("#address"),
            oldId: getVal("#oldId"),
            gender: getVal("#gender"),
            expiry: getVal("#expiry")
        };
    }

    function collectBCardFields() {
        const pane = document.querySelector("#bcard-pane");
        if (!pane) return null;
        const visible = pane.offsetParent !== null && getComputedStyle(pane).display !== "none";
        if (!visible) return null;

        return {
            full_name: getVal("#bcardFullName"),
            email: getVal("#bcardEmail"),
            phone: getVal("#bcardPhone"),
            title: getVal("#bcardTitle"),
            company: getVal("#bcardCompany"),
            address: getVal("#bcardAddress"),
            other_info: getVal("#bcardOtherinfo"),
        };
    }

    function buildScannedPayload() {
        const bcf = collectBCardFields();
        return {
            data: collectFormData(),
            face_image: state.faceDataUrl,
            bcard_fields: bcf || state.lastBCardFields || null,
            bcard_image: state.bcardImageDataUrl || null,
            source_images: state.sourceImages || [],
            last_qr_raw: state.lastQRRaw,
            last_bcard_text: state.lastBCardText,
            registration_id: state.registrationId,
            ts: Date.now()
        };
    }

    /**
     * Fire-and-forget: UI をブロックせずにバックグラウンドでサーバーにペイロードを送信します。
     */
    function _saveRegistrationInBackground(payload) {
        fetch(state.payloadEndpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        })
            .then(r => r.json())
            .then(js => {
                if (js?.qr_url) {
                    const abs = js.qr_url.startsWith("http")
                        ? js.qr_url
                        : new URL(js.qr_url, window.location.origin).toString();
                    state.serverQRUrl = abs;
                    // QR を表示するために #qr-from-server を使用しません (この部分はカードプレビュー画面のみとするため)
                    updateActionButtons();
                }
                log(`Lưu đăng ký nền ${js?.registration_id ? "OK: " + js.registration_id : "xong."}`);
            })
            .catch(err => log("バックグラウンド登録保存エラー: " + (err?.message || err)));
    }
 
    async function sendPayloadToPython(tag = "") {
        if (state.isSubmitting) { log("送信中..."); return; }
        state.isSubmitting = true;
        updateActionButtons();
 
        // 1. 状態を変更する前にペイロードをビルド
        const payload = buildScannedPayload();
 
        // 2. バックグラウンド API 送信 (fire-and-forget, 待機しない)
        _saveRegistrationInBackground(payload);
        log("バックグラウンド登録を送信しました。UI をすぐに表示します...");
 
        // 3. UI を即座に表示 (API の返信を待たない)
        try {
            // これが QR データの保存であり、まだ顔写真がない場合は、そのままストリームを維持して撮影を続行
            if (state.autoCyclePhase === "FACE" && !state.faceDataUrl) {
                log("QR データを保存しました。顔写真の撮影を続行します...");
                return;
            }
 
            await unlockScanCam2();
 
            // カードがある場合 (auto-done)、カードの引き抜きを待つために REMOVING フェーズに移行
            if (state.cardAutoDone) {
                log("登録が成功しました。Cam 1 のロックを維持しています。お客様が名刺を受け取るのを待っています...");
                state.autoCyclePhase = "REMOVING";
                state.emptyGapCount = 0;
                // badge cảm ơn case ocr timeout
                await new Promise((resolve) => setTimeout(resolve, 3000));
                showRemoveCardOverlay(true, true);
                playAudioAndWait(sndRegisterDone);
                // resetAll まで lockScanCam1 を維持
            } else {
                log("登録が完了しました。リセット前に感謝のメッセージを表示します...");
                await lockScanCam1("感謝メッセージを表示");
                await lockScanCam2("感謝メッセージを表示");
                // QR タイムアウト時の感謝バッジ
                await new Promise((resolve) => setTimeout(resolve, 3000));
                showRemoveCardOverlay(true, false); // QR-only case: no card reminder
                await playAudioAndWait(sndRegisterDone);

                setTimeout(() => resetAll(true), 3000);
            }
        } catch (e) {
            log("登録 UI エラー: " + e.message);
        } finally { state.isSubmitting = false; updateActionButtons(); }
    }

    // ====== カメラ 1: 名刺 (名刺検出) ======
    let cam1Started = false;
    async function startCam1() {
        try {
            const video1 = $("#video1"); if (!video1) { log("#video1 がありません"); return; }

            const devId = $("#cam1-select") ? $("#cam1-select").value : undefined;
            const videoConstraints = devId
                ? { deviceId: { exact: devId }, width: { ideal: 1280 }, height: { ideal: 720 } }
                : { facingMode: { ideal: state.cam1Facing || "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } };
            video1.setAttribute("playsinline", ""); video1.muted = true;
            const stream = await navigator.mediaDevices.getUserMedia({ video: videoConstraints, audio: false });
            video1.srcObject = stream;
            await video1.play().catch(() => { });

            cam1Started = true; window.cam1Started = true;
            const p1 = $("#cam1-power"); if (p1) p1.classList.add("on");

            await bumpCam1Resolution(video1);
            log("カメラ 1 (名刺) を起動しました。");

            startAutoCardFromCam1();
        } catch (e) { log("Lỗi bật Camera 1: " + e.message); }
    }

    async function stopCam1() {
        try {
            const v1 = $("#video1");
            if (v1 && v1.srcObject) {
                v1.srcObject.getTracks().forEach(t => t.stop());
                v1.srcObject = null;
            }
            cam1Started = false; window.cam1Started = false;
            const p1 = $("#cam1-power"); if (p1) p1.classList.remove("on");
            log("カメラ 1 を停止しました。");
        } catch (e) { log("カメラ 1 の停止エラー: " + e.message); }
    }

    const btnCam1Start = $("#cam1-start"); if (btnCam1Start) btnCam1Start.addEventListener("click", startCam1);
    const btnCam1Stop = $("#cam1-stop"); if (btnCam1Stop) btnCam1Stop.addEventListener("click", stopCam1);

    // ====== Camera 2: QR (Nimiq) + Face ======
    const btnScanQR = $("#scan-qr"); if (btnScanQR) btnScanQR.addEventListener("click", async () => {
        if (state.scanLockedCam2) { log("Cam 2 のスキャンはロックされています。続行するにはリセットまたは登録を行ってください。"); return; }
        const v2 = $("#video2");
        if (!v2 || !v2.srcObject) { await startCam2(); }
        log("QR をスキャン中...");
    });

    async function bumpCam1Resolution(videoEl) {
        const track = videoEl?.srcObject?.getVideoTracks?.()[0];
        if (!track) return;

        try {
            const caps = track.getCapabilities ? track.getCapabilities() : {};
            console.log("Cam1 capabilities:", caps);

            const canContinuousFocus =
                caps.focusMode && Array.isArray(caps.focusMode) &&
                caps.focusMode.includes("continuous");

            const constraints = {
                width: { min: 640, ideal: 1280, max: 1920 },
                height: { min: 480, ideal: 720, max: 1080 },
                advanced: []
            };

            if (canContinuousFocus) {
                constraints.advanced.push({ focusMode: "continuous" });
            }

            await track.applyConstraints(constraints);
        } catch (e) {
            console.warn("applyConstraints Cam1 failed:", e);
        }

        const s = track.getSettings?.() || {};
        log(`Cam1 settings: ${s.width}x${s.height}, focus=${s.focusMode || "-"}`);
    }

    const sharedCanvas1 = document.createElement("canvas");
    async function grabFromVideo1(targetWidth = 0) {
        const v = $("#video1");
        if (!v || !v.videoWidth) throw new Error("Camera 1 chưa sẵn sàng");

        const c = sharedCanvas1;
        let tw = v.videoWidth;
        let th = v.videoHeight;

        if (targetWidth > 0 && targetWidth < tw) {
            const scale = targetWidth / tw;
            tw = targetWidth;
            th = Math.round(v.videoHeight * scale);
        }

        if (c.width !== tw || c.height !== th) {
            c.width = tw;
            c.height = th;
        }

        const ctx = c.getContext("2d", { alpha: false });
        ctx.drawImage(v, 0, 0, tw, th);
        return c;
    }

    // ====== キャンバスオーバーレイへの名刺検出境界ボックス (bbox) の描画 ======
    function drawCardBbox(bbox, stableCount, required, triggered) {
        // [OPTIMIZATION] Commented out drawing logic to reduce CPU usage on Raspberry Pi
        /*
        const vid = $("#video1");
        const cvs = $("#card-bbox-canvas");
        if (!cvs || !vid) return;

        // Sync canvas size với video element (display size)
        const dispW = vid.clientWidth;
        const dispH = vid.clientHeight;
        if (cvs.width !== dispW || cvs.height !== dispH) {
            cvs.width = dispW;
            cvs.height = dispH;
        }

        const ctx = cvs.getContext("2d");
        ctx.clearRect(0, 0, dispW, dispH);

        if (!bbox) return;

        // 元のフレーム座標からの bbox を表示キャンバス座標にマップ
        const vidW = vid.videoWidth || dispW;
        const vidH = vid.videoHeight || dispH;
        const scaleX = dispW / vidW;
        const scaleY = dispH / vidH;

        const rx = bbox.x1 * scaleX;
        const ry = bbox.y1 * scaleY;
        const rw = (bbox.x2 - bbox.x1) * scaleX;
        const rh = (bbox.y2 - bbox.y1) * scaleY;

        if (triggered) {
            // 撮影時に緑色に点滅
            ctx.strokeStyle = "#00ff44";
            ctx.lineWidth = 4;
            ctx.shadowColor = "#00ff44";
            ctx.shadowBlur = 12;
        } else {
            // トラッキング中は黄色
            const ratio = Math.min(stableCount / required, 1);
            ctx.strokeStyle = `hsl(${Math.round(ratio * 120)}, 100%, 55%)`; // 赤→黄→緑
        }

        ctx.strokeRect(rx, ry, rw, rh);
        ctx.shadowBlur = 0;

        let label = triggered
            ? "✓ スキャン完了"
            : (stableCount > required / 2 ? "⚠️ そのままお待ちください" : "");

        if (state.autoCyclePhase === "REMOVING" && triggered) {
            label = "⚠️ 名刺をお取りください";
        }

        ctx.font = "bold 13px monospace";
        ctx.fillStyle = ctx.strokeStyle;
        const pad = 4;
        const labelY = ry > 22 ? ry - pad : ry + 18;
        ctx.fillText(label, rx + pad, labelY);
        */
    }

    function clearCardBbox() {
        const cvs = $("#card-bbox-canvas");
        if (!cvs) return;
        cvs.getContext("2d").clearRect(0, 0, cvs.width, cvs.height);
    }
 
    // ====== Camera 1 からの名刺自動検出ループ (YOLOv8) ======
    async function startAutoCardFromCam1() {
        const vid = $("#video1");
        if (!vid) return;

        // 現在のライフサイクルですでに結果がある場合は、実行しない
        if (state.cardAutoDone || state.cardAutoSending) return;
 
        // カメラが準備できていない場合は、1秒待機してから再試行
        if (!vid.srcObject) {
            setTimeout(startAutoCardFromCam1, 1000);
            return;
        }

        state.cardAutoSending = true;
        const frameDelay = Math.max(1, Math.round(1000 / (state.cardAutoFps || 1)));

        log("Camera 1 からの名刺自動検出 (YOLOv8) を有効にしました。");

        while (state.cardAutoSending && vid.srcObject) {
            // Cam 1 がロックされているか、名刺を取り出している最中ではないのにスキャンが完了している場合は、ループを一時休止（ポーズ）
            if (state.scanLockedCam1 || state.cardAutoDone) {
                if (state.autoCyclePhase !== "REMOVING") {
                    await new Promise((r) => setTimeout(r, 600));
                    continue;
                }
            }
            try {
                // レイヤーの彩度不足による誤認を避けるため解像度を上げる:
                // 名刺取り出し画面 (REMOVING) または挨拶画面では、Bbox のズレを防ぎ API で鮮明に認識させるため 1280px を使用。
                // 通常のスキャンフェーズ (IDLE) では、速度向上のため引き続き 640px を使用。
                const isSensitivePhase = state.welcomeEyesVisible || state.autoCyclePhase === "REMOVING";
                const grabRes = isSensitivePhase ? 1280 : 640;
                const imgQuality = isSensitivePhase ? 0.75 : 0.5;
                const reducedCanvas = await grabFromVideo1(grabRes);

                const blob = await new Promise((resolve) =>
                    reducedCanvas.toBlob(resolve, "image/jpeg", imgQuality)
                );
                const fd = new FormData();
                fd.append("frame", blob, `card-${Date.now()}.jpg`);
                fd.append("ts", String(Date.now()));
                if (state.autoCyclePhase === "REMOVING") {
                    fd.append("check_only", "true");
                }

                const res = await fetch(state.cardAutoEndpoint, {
                    method: "POST",
                    body: fd,
                });
                const js = await res.json().catch(() => null);

                if (js && js.ok) {
                    const hasBbox = !!js.bbox;

                    // --- 新規: 挨拶画面（Welcome Eyes）中の衛生チェック ---
                    if (state.welcomeEyesVisible && hasBbox) {
                        log("待機画面中にトレイ上の不要なカードを検出しました。取り出しを要求します。");
                        state.autoCyclePhase = "REMOVING";
                        state.emptyGapCount = 0;
                        showWelcomeEyes(false); // Ẩn mắt đi
                        showRemoveCardOverlay(true, true); // Hiện yêu cầu rút thẻ
                        // Sẽ lọt xuống nhánh REMOVING xử lý tiếp
                    }


                    if (state.autoCyclePhase === "REMOVING") {
                        // ... (REMOVING ロジックを維持)
                        if (hasBbox) {
                            if (state.emptyGapCount > 0) {
                                log(`まだカードを検出しています (カウントをリセット)。`);
                                state.emptyGapCount = 0;
                            }
                            drawCardBbox(js.bbox, js.required, js.required, true);
                        } else {
                            state.emptyGapCount++;
                            // フリッカー（Bbox の消失）による誤った取り出し判定を防ぐため、連続した空きフレームの必要数を増やす
                            const actualGapRequired = state.welcomeEyesVisible || state.autoCyclePhase === "REMOVING" ? 2 : state.emptyGapRequired;

                            if (state.emptyGapCount % 5 === 0 || state.emptyGapCount === 1) {
                                log(`Đang đợi lấy thẻ... Gap count: ${state.emptyGapCount}/${actualGapRequired}`);
                            }
                            if (state.emptyGapCount >= actualGapRequired) {
                                log("カードの取り出しを確認しました。感謝バッジの表示時間を確認しています...");
                                const waitTime = (state.removingPhaseMinUntil || 0) - Date.now();
                                if (waitTime > 0) {
                                    log(`感謝バッジの表示が完了するまであと ${waitTime}ms 待機します...`);
                                    await new Promise(r => setTimeout(r, waitTime));
                                }
                                if (state.autoCyclePhase === "REMOVING") {
                                    log("バッジの表示が完了しました。システムは IDLE 状態に戻ります。");
                                    resetAll(true);
                                }
                            }
                        }
                    } else {
                        // Nếu vẫn đang bật Đôi mắt (chưa có người) mà không có lỗi rác ở trên, thì chỉ pause nhẹ và quét tiếp
                        if (state.welcomeEyesVisible) {
                            await new Promise((r) => setTimeout(r, 600));
                            continue;
                        }

                        // Phase bình thường: Chờ trigger capture
                        if (js.card_detected) {
                            drawCardBbox(js.bbox, js.required, js.required, true);
                            setTimeout(clearCardBbox, 800);

                            log(`✅ 名刺をキャプチャしました。直ちに顔撮影に切り替え、名刺の OCR はバックグラウンドで処理します。`);
                            state.autoCyclePhase = "CARD";
                            state.suppressGreetingDuringCompletion = true;
                            if (typeof startBCardProcessingUI === "function") startBCardProcessingUI();

                            // 重複キャプチャを防ぐため Cam 1 をロック。顔/プレゼンスを継続させるため Cam 2 は開いたままにする
                            try {
                                await lockScanCam1("Đã tự động chụp danh thiếp");
                                state.cardAutoDone = true;
                                state.cardRetryRequested = false;
                            } catch { }

                            // STRATEGY 4: Giảm OCR resolution (1280px đủ để đọc chính xác mà nhanh hơn full-res)
                            const ocrCanvas = await grabFromVideo1(1280);

                            // STRATEGY 2: Async card image encoding (toBlob + FileReader)
                            ocrCanvas.toBlob((blob) => {
                                if (!blob) return;
                                const reader = new FileReader();
                                reader.onloadend = () => {
                                    state.bcardImageDataUrl = reader.result;
                                    const qrImg = $("#qr-from-server");
                                    if (qrImg) {
                                        qrImg.src = state.bcardImageDataUrl;
                                        qrImg.style.display = "block";
                                    }
                                };
                                reader.readAsDataURL(blob);
                            }, "image/jpeg", 0.85);

                            state.sessionFinalizeTriggered = false;

                            // OCR (自動スキャン) とリンクさせるため、早めに登録 ID を生成
                            if (!state.registrationId) {
                                const now = new Date();
                                const dateStr = [
                                    now.getDate().toString().padStart(2, '0'),
                                    (now.getMonth() + 1).toString().padStart(2, '0'),
                                    now.getFullYear()
                                ].join('-');
                                const timeStr = [
                                    now.getHours().toString().padStart(2, '0'),
                                    now.getMinutes().toString().padStart(2, '0'),
                                    now.getSeconds().toString().padStart(2, '0')
                                ].join('-');
                                const suffix = Math.random().toString(36).substring(2, 6);
                                state.registrationId = `REG_${dateStr}_${timeStr}_${suffix}`;
                                log(`[自動スキャン] 登録 ID を早期生成しました: ${state.registrationId}`);
                            }

                            await startAsyncBcardOcrFromCanvas(ocrCanvas);
                            await startFaceGuidanceAndArmCapture();
                        } else {
                            // Chưa trigger – vẽ bbox tracking
                            const stableCount = js.stable_count || 0;
                            const required = js.required || 15;
                            drawCardBbox(js.bbox, stableCount, required, false);
                        }
                    }
                }
            } catch (err) {
                log("Auto card detect lỗi: " + (err?.message || err));
            }

            // Nghỉ giữa các lần gửi frame
            await new Promise((r) => setTimeout(r, frameDelay));
        }

        state.cardAutoSending = false;
    }



    function startBCardProcessingUI() {
        if (btnScanBCard) {
            btnScanBCard.disabled = true;
            btnScanBCard.textContent = "名刺を解析中...";
        }
        toggleBCardPane(false);
        fillBCardPane({}, "OCR中...");
    }

    function finishBCardProcessingUI(fields, text) {
        if (fields) {
            state.lastBCardFields = fields;
            state.lastBCardText = text || "";
            fillBCardPane(fields, text || "");
        }
        updateActionButtons();
        if (btnScanBCard) {
            btnScanBCard.disabled = false;
            btnScanBCard.textContent = "1 → 名刺をスキャン";
        }
        // ✅ Chuyển sang phase FACE nếu đã đủ thông tin (cả cho trường hợp quét tay)
        if (state.autoCyclePhase === "IDLE" && (hasBcardData() || hasCccdData())) {
            state.autoCyclePhase = "FACE";
        }
        // ✅ Luôn đảm bảo presence sẵn sàng
        state.allowPresence = true;
        if (!state.faceDataUrl && !state.presenceSending) {
            startPresenceStream();
        }
    }

    function _bcardIdentity(fields = {}) {
        const name = (fields.full_name || fields.name || "").trim();
        const company = (fields.company || fields.org || "").trim();
        return { name, company };
    }

    function hasRequiredBcardIdentity(fields = {}) {
        const idf = _bcardIdentity(fields);
        // 制限緩和: データの漏れを防ぐため、名前またはメール/電話番号のいずれかがあれば保存を許可
        return !!(idf.name || fields.email || fields.phone || fields.tel);
    }


    function stopBcardOcrPolling() {
        if (state.ocrPollHandle) {
            clearInterval(state.ocrPollHandle);
            state.ocrPollHandle = null;
        }
    }

    async function requestCardRecapture(reason = "") {
        if (state.cardRetryRequested) return;
        state.cardRetryRequested = true;
        state.cardRetryCount++;
        state.sessionFinalizeTriggered = false;
        stopBcardOcrPolling();
        state.ocrTaskId = null;
        state.ocrStatus = "idle";
        state.lastBCardFields = null;
        state.lastBCardText = "";
        state.bcardImageDataUrl = null;
        state.registrationId = null;
        const qrImg = $("#qr-from-server");
        if (qrImg) { qrImg.src = ""; qrImg.style.display = "none"; }

        const isRetry = state.cardRetryCount > 1;

        log(`名刺情報 (氏名/会社名) が不足しています${reason ? `: ${reason}` : ""}。名刺を撮り直してください。`);
        interruptAndClearAudioQueue();

        if (!isRetry) {
            // まだ再生されていない場合は、顔撮影の案内音声をキャンセル (初回のみ)
            if (state.faceGuideAudioTimer) {
                clearTimeout(state.faceGuideAudioTimer);
                state.faceGuideAudioTimer = null;
            }
            if (state.faceRetryTimer) {
                clearTimeout(state.faceRetryTimer);
                state.faceRetryTimer = null;
            }
            state.faceRetryCount = 0;
            state.faceCaptureReadyAtMs = Number.MAX_SAFE_INTEGER;
        }

        await playAudioAndWait(sndCardUnread, true);

        state.cardAutoDone = false;
        state.cardAutoSending = false;
        state.autoCyclePhase = "IDLE";
        state.allowPresence = true;

        await unlockScanCam1();
        startAutoCardFromCam1();
    }

    async function tryFinalizeSessionAfterFaceAndOcr(source = "") {
        if (state.sessionFinalizeTriggered || state.isSubmitting) return;
        if (!state.faceDataUrl) return;

        log(`[Finalize] Face OK. Hiện Badge ngay${source ? ` (${source})` : ""}.`);
        state.sessionFinalizeTriggered = true;
        state.cardRetryRequested = false;
        _showThankYouAndDeferredSave();
    }

    /**
     * 感謝バッジを即座に表示しますが、登録の保存は OCR が完了するまで待機します。
     * リセット (resetAll) によるデータ消失や新しいセッションによる上書きを防ぐため、クロージャスナップショットを使用します。
     */
    async function _showThankYouAndDeferredSave() {
        // 0. バッジを最速で表示するため、現在再生中の音声 (例: huongdanchupface.mp3) を即座に停止
        interruptAndClearAudioQueue();

        // 1. 現在の全データをローカル変数 (クロージャ) にスナップショットとして保存
        const taskId = state.ocrTaskId;

        const snapshot = {
            data: collectFormData(),
            face_image: state.faceDataUrl,
            bcard_image: state.bcardImageDataUrl || null,
            source_images: [...(state.sourceImages || [])],
            last_qr_raw: state.lastQRRaw,
            last_bcard_text: state.lastBCardText,
            registration_id: state.registrationId,
            ts: Date.now()
        };

        // 2. 即座に感謝 UI を表示 (Fire-and-forget UI)
        await unlockScanCam2();
        const hasCard = !!state.cardAutoDone;

        if (hasCard) {
            state.autoCyclePhase = "REMOVING";
            state.emptyGapCount = 0;
            state.removingPhaseMinUntil = Date.now() + 3000;
            if (state.thankYouResetTimer) clearTimeout(state.thankYouResetTimer);
            state.thankYouResetTimer = setTimeout(async () => {
                state.thankYouResetTimer = null;
                if (state.autoCyclePhase !== "REMOVING") return;
                showRemoveCardOverlay(true, true);
                await playAudioAndWait(sndRegisterDone);
            }, 100);
        } else {
            await lockScanCam1("感謝表示");
            await lockScanCam2("感謝表示");
            if (state.thankYouResetTimer) clearTimeout(state.thankYouResetTimer);
            state.thankYouResetTimer = setTimeout(async () => {
                state.thankYouResetTimer = null;
                showRemoveCardOverlay(true, false);
                await playAudioAndWait(sndRegisterDone);
                if (state.thankYouResetTimer) clearTimeout(state.thankYouResetTimer);
                state.thankYouResetTimer = setTimeout(() => resetAll(true), 3000);
            }, 100);
        }

        // 3. サーバーに直ちに登録を送信 (シェルデータ)
        // OCR 結果は完了後、(registrationId に基づいて) サーバーによってこのレコードに自動的に更新されます
        log(`[バックグラウンド] サーバーに登録情報を送信中 (reg_id: ${state.registrationId})...`);
        _saveRegistrationInBackground(snapshot);
    }

    async function pollBcardOcrStatus(taskId) {
        if (!taskId) return;
        stopBcardOcrPolling();

        const pollInterval = setInterval(async () => {
            try {
                if (state.ocrTaskId !== taskId) { clearInterval(pollInterval); return; }

                const url = `${state.bcardOCRAsyncStatusBase}/${encodeURIComponent(taskId)}?t=${Date.now()}`;
                const res = await fetch(url, { method: "GET" });
                const js = await res.json().catch(() => ({}));
                if (!res.ok || !js?.ok) return;

                const status = (js.status || "").toLowerCase();
                if (status === "processing") {
                    state.ocrStatus = "processing";
                    return;
                }
                if (status === "done") {
                    clearInterval(pollInterval);
                    state.ocrStatus = "done";
                    state.ocrTaskId = null;
                    stopBcardOcrPolling();

                    const fields = js.fields || {};
                    finishBCardProcessingUI(fields, js.text || "");

                    await tryFinalizeSessionAfterFaceAndOcr("ocr-done");
                    return;
                }
                if (status === "error") {
                    clearInterval(pollInterval);
                    state.ocrStatus = "error";
                    log(`OCR Task ${taskId} error: ${js.error}`);
                }
            } catch (err) {
                console.warn("poll OCR status error:", err);
            }
        }, 800);

        state.ocrPollHandle = pollInterval;
    }


    async function startAsyncBcardOcrFromCanvas(canvas) {
        try {
            const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.9));
            const fd = new FormData();
            fd.append("image", blob, `bcard-${Date.now()}.jpg`);
            if (state.registrationId) {
                fd.append("reg_id", state.registrationId);
            }

            const endpoint = state.bcardOCRAsyncStartEndpoint;
            console.log(`[OCR Start] Sending req to ${endpoint} with reg_id=${state.registrationId}`);

            const res = await fetch(endpoint, { method: "POST", body: fd });
            const js = await res.json().catch(() => ({}));
            if (!res.ok || !js?.ok || !js?.task_id) {
                throw new Error(js?.error || "Cannot start async OCR");
            }

            state.ocrTaskId = js.task_id;
            state.ocrStatus = "processing";
            pollBcardOcrStatus(js.task_id);

            return js.task_id;
        } catch (e) {
            state.ocrStatus = "error";
            log("Lỗi khởi tạo OCR: " + e.message);
            return null;
        }
    }

    async function handleScanBCard() {
        try {
            if (state.scanLockedCam1) { log("Đang khoá quét Cam 1. Reset/Đăng ký để tiếp tục."); return; }

            startBCardProcessingUI();

            const c = await grabFromVideo1();
            state.bcardImageDataUrl = c.toDataURL("image/jpeg", 0.9);
            await lockScanCam1("Đã chụp danh thiếp");

            state.cardAutoDone = true;
            state.cardRetryRequested = false;
            state.sessionFinalizeTriggered = false;

            // Generate Registration ID sớm để link với OCR
            if (!state.registrationId) {
                const now = new Date();
                const dateStr = [
                    now.getDate().toString().padStart(2, '0'),
                    (now.getMonth() + 1).toString().padStart(2, '0'),
                    now.getFullYear()
                ].join('-');
                const timeStr = [
                    now.getHours().toString().padStart(2, '0'),
                    now.getMinutes().toString().padStart(2, '0'),
                    now.getSeconds().toString().padStart(2, '0')
                ].join('-');
                const suffix = Math.random().toString(36).substring(2, 6);
                state.registrationId = `REG_${dateStr}_${timeStr}_${suffix}`;
                log(`Đã tạo Registration ID sớm: ${state.registrationId}`);
            }

            await startAsyncBcardOcrFromCanvas(c);

            if (!state.faceDataUrl) {
                await startFaceGuidanceAndArmCapture();
            } else {
                await tryFinalizeSessionAfterFaceAndOcr("manual-card");
            }

        } catch (e) {
            log("Lỗi OCR danh thiếp: " + e.message);
            toggleBCardPane(false); toggleCCCDForm(true);
            if (btnScanBCard) {
                btnScanBCard.disabled = false;
                btnScanBCard.textContent = "1 → 名刺をスキャン";
            }
        }
    }
    const btnScanBCard = $("#scan-bcard");
    if (btnScanBCard) btnScanBCard.addEventListener("click", handleScanBCard);

    function ddMMyyyyToISO(s) {
        if (!s) return "";
        s = s.replace(/[^\d]/g, "").trim();
        if (s.length === 8) {
            const dd = s.slice(0, 2), mm = s.slice(2, 4), yyyy = s.slice(4, 8);
            return `${yyyy}-${mm}-${dd}`;
        }
        const m = s.match(/^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})$/);
        if (m) {
            const dd = m[1].padStart(2, "0"), mm = m[2].padStart(2, "0"), yyyy = m[3];
            return `${yyyy}-${mm}-${dd}`;
        }
        return "";
    }

    function parseVNIdQr(text) {
        const parts = (text || "").trim().split("|");
        if (parts.length < 7) return null;
        const cccd = (parts[0] || "").trim();
        const oldId = (parts[1] || "").trim();
        const fullName = (parts[2] || "").trim();
        const dob = ddMMyyyyToISO((parts[3] || "").trim());
        let gender = (parts[4] || "").trim();
        if (/^(m|nam)$/i.test(gender)) gender = "Nam";
        else if (/^(f|nu|nữ)$/i.test(gender)) gender = "Nữ";
        const address = (parts[5] || "").trim();
        const expiry = ddMMyyyyToISO((parts[6] || "").trim());
        if (!/^\d{9,12}$/.test(cccd)) return null;
        return { cccd, oldId, fullName, dob, gender, address, expiry };
    }

    function tryFillFromQR(text) {
        let parsedSomething = false;
        const vn = parseVNIdQr(text);
        if (vn) {
            parsedSomething = true;
            if (vn.fullName) setVal("#fullName", vn.fullName);
            if (vn.cccd) setVal("#idNumber", vn.cccd);
            setVal("#oldId", vn.oldId || "");
            setVal("#dob", vn.dob || "");
            setVal("#gender", vn.gender || "");
            setVal("#address", vn.address || "");
            setVal("#expiry", vn.expiry || "");
            updateActionButtons();
            return hasCccdData() || hasBcardData();
        }
        try {
            const obj = JSON.parse(text);
            parsedSomething = true;
            if (obj.fullName) setVal("#fullName", obj.fullName);
            else if (obj.bcard_fields && (obj.bcard_fields.full_name || obj.bcard_fields.name)) {
                setVal("#fullName", obj.bcard_fields.full_name || obj.bcard_fields.name);
            }
            if (obj.idNumber) setVal("#idNumber", obj.idNumber);
            if (obj.oldId) setVal("#oldId", obj.oldId);
            if (obj.dob) setVal("#dob", obj.dob);
            if (obj.gender) setVal("#gender", obj.gender);
            if (obj.address) setVal("#address", obj.address);
            if (obj.expiry) setVal("#expiry", obj.expiry);

            if (obj.bcard_fields) {
                state.lastBCardFields = obj.bcard_fields;
                toggleCCCDForm(false);
                toggleBCardPane(true);
                fillBCardPane(obj.bcard_fields, obj.last_bcard_text || "");
            } else if (obj.bcard_name || obj.bcard_company || obj.bcard_email || obj.bcard_phone) {
                // Support flattened keys from older/alternative versions
                const bcf = {
                    full_name: obj.bcard_name,
                    company: obj.bcard_company,
                    email: obj.bcard_email,
                    phone: obj.bcard_phone,
                    title: obj.bcard_title || "",
                    address: obj.bcard_address || "",
                    other_info: obj.bcard_info || obj.other_info || ""
                };
                state.lastBCardFields = bcf;
                toggleCCCDForm(false);
                toggleBCardPane(true);
                fillBCardPane(bcf, obj.last_bcard_text || "");
                // ✅ Chuyển sang phase FACE nếu quét QR có đủ thông tin
                if (state.autoCyclePhase === "IDLE" && (hasBcardData() || hasCccdData())) {
                    state.autoCyclePhase = "FACE";
                }
            }
        } catch { }
        // Fallback id regex chỉ để hỗ trợ điền form, KHÔNG dùng để xác thực QR hợp lệ.
        const idMatch = text.match(/\b\d{9,12}\b/);
        if (idMatch) {
            parsedSomething = true;
            setVal("#idNumber", idMatch[0]);
        }

        updateActionButtons();
        const sufficient = hasCccdData() || hasBcardData();
        if (!sufficient && parsedSomething) {
            log("QR đã đọc nhưng chưa đủ dữ liệu hợp lệ cho quy trình.");
        }
        return sufficient;
    }

    function syncVideo2Aspect() {
        const vid = $("#video2"); if (!vid) return;
        vid.style.objectFit = "contain";
        if (vid.videoWidth && vid.videoHeight) vid.style.aspectRatio = `${vid.videoWidth} / ${vid.videoHeight}`;
        else vid.style.aspectRatio = "auto";
    }

    function ensureVideo2ZoomCanvas() {
        const vid = $("#video2");
        if (!vid) return null;
        const wrap = vid.closest(".video-wrap") || vid.parentElement;
        if (!wrap) return null;

        // đảm bảo wrapper có position
        const cs = getComputedStyle(wrap);
        if (!cs.position || cs.position === "static") {
            wrap.style.position = "relative";
        }

        let cvs = document.getElementById("video2-zoom");
        if (!cvs) {
            cvs = document.createElement("canvas");
            cvs.id = "video2-zoom";
            Object.assign(cvs.style, {
                position: "absolute",
                inset: "0",
                width: "100%",
                height: "100%",
                display: "block",
                zIndex: "1",
            });
            // cho canvas zoom nằm dưới canvas2 (vẽ khung xanh)
            wrap.insertBefore(cvs, wrap.firstChild);
        }

        // ẩn video gốc, chỉ dùng làm nguồn
        vid.style.opacity = "0";
        vid.style.pointerEvents = "none";

        return cvs;
    }

    function stopVideo2ZoomLoop() {
        state.video2ZoomRunning = false;
        if (state.video2ZoomHandle) {
            cancelAnimationFrame(state.video2ZoomHandle);
            state.video2ZoomHandle = null;
        }
        const cvs = document.getElementById("video2-zoom");
        if (cvs) {
            const ctx = cvs.getContext("2d");
            ctx.clearRect(0, 0, cvs.width, cvs.height);
        }
    }

    // ズーム 3x のフレームをキャンバスに描画するループ
    function startVideo2ZoomLoop() {
        const vid = $("#video2");
        if (!vid) return;
        const cvs = ensureVideo2ZoomCanvas();
        if (!cvs) return;

        if (state.video2ZoomRunning) return;
        state.video2ZoomRunning = true;

        const ctx = cvs.getContext("2d");
        const wrap = vid.closest(".video-wrap") || vid.parentElement;

        const targetMs = 1000 / 12; // ~12fps CPU 節約のため
        let last = 0;

        function loop(now) {
            if (!state.video2ZoomRunning || !vid.srcObject) {
                state.video2ZoomRunning = false;
                return;
            }

            if (!last || now - last >= targetMs) {
                last = now;

                const vw = vid.videoWidth || 0;
                const vh = vid.videoHeight || 0;
                if (vw && vh) {
                    const rect = wrap.getBoundingClientRect();
                    if (cvs.width !== rect.width || cvs.height !== rect.height) {
                        cvs.width = rect.width;
                        cvs.height = rect.height;
                    }

                    const zoom = state.video2ZoomFactor || 1;
                    const cropW = Math.floor(vw / zoom);
                    const cropH = Math.floor(vh / zoom);
                    const cropX = Math.floor((vw - cropW) / 2);
                    const cropY = Math.floor((vh - cropH) / 2);
 
                    ctx.clearRect(0, 0, cvs.width, cvs.height);
 
                    const isMirrored = wrap.classList.contains("mirror");
                    if (isMirrored) {
                        ctx.save();
                        ctx.translate(cvs.width, 0);
                        ctx.scale(-1, 1);
                    }

                    ctx.drawImage(
                        vid,
                        cropX, cropY, cropW, cropH,   // 中央領域 (ズーム)
                        0, 0, cvs.width, cvs.height   // フル表示フレーム
                    );

                    if (isMirrored) ctx.restore();
                }
            }

            state.video2ZoomHandle = requestAnimationFrame(loop);
        }

        state.video2ZoomHandle = requestAnimationFrame(loop);
    }

    async function startCam2() {
        try {
            if (!window.QrScanner) { log("qr-scanner が準備できていません。"); return; }
            const vid = $("#video2"); if (!vid) { log("#video2 がありません"); return; }

            if (state.qrScanner) { try { await state.qrScanner.destroy(); } catch { } state.qrScanner = null; }
            const overlay = $("#scan-overlay");

            state.qrScanner = new window.QrScanner(
                vid,
                async (result) => {
                    // 待機画面が表示されている場合、または Cam 2 がロックされている場合は QR スキャンを無視
                    if (state.welcomeEyesVisible || state.scanLockedCam2) return;

                    const content = (typeof result === "string") ? result : (result?.data || "");
                    if (!content) return;
                    state.lastQRRaw = content;
                    log("QR: " + content);
                    const isValidQr = tryFillFromQR(content);
                    if (!isValidQr) {
                        if (state.handlingInvalidQr) return;
                        state.handlingInvalidQr = true;
                        await lockScanCam2("無効な QR");
                        await lockScanCam1("無効な QR");
                        log("無効な QR です。通知後にシステムをリセットします。");
                        await playAudioAndWait(sndQrInvalid, true);
                        await new Promise((r) => setTimeout(r, 2000));
                        await resetAll(true);
                        state.handlingInvalidQr = false;
                        return;
                    }
                    await lockScanCam2("QR 読み取り完了");
                    await lockScanCam1("QR 読み取り完了");
 
                    // state bcardImageDataUrl を更新 (カメラからの画像を一時保存)
                    const v2 = $("#video2");
                    if (v2 && v2.videoWidth) {
                        const off = document.createElement("canvas");
                        off.width = v2.videoWidth;
                        off.height = v2.videoHeight;
                        const ctx = off.getContext("2d");
                        ctx.drawImage(v2, 0, 0, off.width, off.height);
                        state.bcardImageDataUrl = off.toDataURL("image/jpeg", 0.9);
                        const qrImg = $("#qr-from-server");
                        if (qrImg) {
                            qrImg.src = state.bcardImageDataUrl;
                            qrImg.style.display = "block";
                        }
                    }


                    // 自動的に顔撮影フェーズに移行 (すぐには保存せず、顔撮影完了後に同時保存)
                    if (state.autoCyclePhase === "IDLE") {
                        state.autoCyclePhase = "FACE";
                        log("QR を受信しました。完了するために顔認識を待機中...");
                    }
                },
                {
                    returnDetailedScanResult: true,
                    highlightScanRegion: true,
                    highlightCodeOutline: true,
                    overlay: overlay,
                    preferredCamera: ($("#cam2-select") && $("#cam2-select").value) || state.cam2Facing,
                    maxScansPerSecond: 15,
                }
            );
            vid.setAttribute("playsinline", ""); vid.muted = true;
            const onMeta = async () => {
                syncVideo2Aspect();
                try { await vid.play(); } catch { }
                setCam2MirrorUI();
                startVideo2ZoomLoop();
                if (!state.presenceSending) {
                    startPresenceStream();
                }
                vid.removeEventListener("loadedmetadata", onMeta);
            };
            vid.addEventListener("loadedmetadata", onMeta);

            await state.qrScanner.start();
            await bumpCam2Resolution(vid);

            const p2 = $("#cam2-power"); if (p2) p2.classList.add("on");
            log("カメラ 2 (QR + 顔) を起動しました。");
        } catch (e) { log("Lỗi bật Camera 2: " + e.message); }
    }

    async function bumpCam2Resolution(videoEl) {
        const track = videoEl?.srcObject?.getVideoTracks?.()[0];
        if (!track) return;
        try {
            const caps = track.getCapabilities ? track.getCapabilities() : {};
            const canContinuousFocus = caps.focusMode && Array.isArray(caps.focusMode) && caps.focusMode.includes("continuous");
            const constraints = {
                width: { min: 640, ideal: 1280, max: 1920 },
                height: { min: 480, ideal: 720, max: 1080 },
                advanced: canContinuousFocus ? [{ focusMode: "continuous" }] : []
            };
            await track.applyConstraints(constraints);
        } catch (e) { console.warn("applyConstraints Cam2 failed:", e); }
        const s = track.getSettings?.() || {};
        log(`Cam2 設定: ${s.width}x${s.height}`);
    }

    async function stopCam2() {
        const vid = $("#video2"); if (!vid) return;
        state.presenceSending = false;
        if (state.qrScanner) {
            try { await state.qrScanner.stop(); await state.qrScanner.destroy(); } catch { }
            state.qrScanner = null;
        }
        const s = vid.srcObject; if (s) { s.getTracks().forEach(t => t.stop()); vid.srcObject = null; }
        const p2 = $("#cam2-power"); if (p2) p2.classList.remove("on");
        stopVideo2ZoomLoop();
        log("カメラ 2 を停止しました。");
    }

    async function flipCam1() {
        state.cam1Facing = state.cam1Facing === "user" ? "environment" : "user";
        try {
            await stopCam1(); await startCam1();
            log("Cam1 facing: " + state.cam1Facing);
        } catch (e) { log("Cam1 反転エラー: " + e.message); }
    }

    async function flipCam2() {
        state.cam2Facing = state.cam2Facing === "user" ? "environment" : "user";
        try {
            if (state.qrScanner && state.qrScanner.setCamera) {
                await state.qrScanner.setCamera(state.cam2Facing).catch(async () => {
                    await stopCam2();
                    const sel = $("#cam2-select"); if (sel) sel.value = "";
                    await startCam2();
                });
            } else {
                await stopCam2();
                const sel = $("#cam2-select"); if (sel) sel.value = "";
                await startCam2();
            }
            setCam2MirrorUI();
            log("Cam2 facing: " + state.cam2Facing);
        } catch (e) { log("Cam2 反転エラー: " + e.message); }
    }

    function setCam2MirrorUI() {
        const wrap = $("#video2")?.closest(".video-wrap");
        if (!wrap) return;
        if (state.cam2Facing === "user") wrap.classList.add("mirror");
        else wrap.classList.remove("mirror");
    }

    window.flipCam1 = flipCam1;
    window.flipCam2 = flipCam2;

    /**
     * ビデオ用の反転ボタンを作成します。
     * @param {string} videoSel ビデオ要素のセレクター
     * @param {string} btnId ボタンの ID
     * @param {string} title ボタンのツールチップ
     * @param {string} label ボタンのラベル
     */
    function ensureFlipButtonForVideo(videoSel, btnId, title = "反転", label = "↺") {
        const v = document.querySelector(videoSel);
        if (!v) return null;
        const wrap = v.closest('.video-wrap') || v.parentElement;
        if (!wrap) return null;

        let btn = document.getElementById(btnId);
        if (!btn) {
            btn = document.createElement('button');
            btn.id = btnId;
            btn.className = 'power-btn';
            btn.title = title;
            btn.type = 'button';
            btn.textContent = label;
            btn.style.right = '72px'; // 電源ボタンと重ならないように調整
            wrap.appendChild(btn);
        }
        return btn;
    }

    const cam1Flip = ensureFlipButtonForVideo('#video1', 'cam1-flip', 'Flip Cam1');
    if (cam1Flip) cam1Flip.addEventListener('click', flipCam1);

    const cam2Flip = ensureFlipButtonForVideo('#video2', 'cam2-flip', 'Flip Cam2');
    if (cam2Flip) cam2Flip.addEventListener('click', flipCam2);

    async function startPresenceStream() {
        const vid = $("#video2"); if (!vid) return;
        if (!vid.srcObject) { log("Presence 用の Cam2 ストリームがありません。"); return; }

        if (!state.allowPresence) {
            log("Presence は一時的にオフです (allowPresence=false)。");
            return;
        }

        // Đã có ảnh khuôn mặt rồi thì khỏi nhận diện nữa
        if (state.faceDataUrl) {
            log("顔写真があるため、Presence を開始しません。");
            return;
        }

        if (state.presenceSending) {
            log("Presence ストリームは既に実行中です。");
            return;
        }

        // 世代カウンター: 開始ごとに +1。
        // 古いループが実行中のまま世代が変わった場合、古いループは自動停止します。
        const myGen = ++state._presenceGeneration;

        state.presenceSending = true;
        state.lastPersonBox = null; state.lastFrameSize = null;
        //123
        state.faceSeenSinceMs = 0;
        state.faceAutoCaptured = false;

        state.presenceLastHadPerson = false;
        state.presenceLastGreetTs = 0;
        state.presenceLastFaceCount = 0;
        state.presenceFaceIncreaseStreak = 0;
        //123
        // Haar cascade には標準的な信頼度がないため、0.0 に設定 — 最良の結果があれば顔とみなします
        state.presenceMinConf = 0.0;

        const targetW = 480;
        const sharedPresenceCanvas = document.createElement("canvas");

        const getBlobAndSize = () => new Promise((resolve) => {
            const off = sharedPresenceCanvas;
            const vw = vid.videoWidth || 640, vh = vid.videoHeight || 360;

            const zoom = state.video2ZoomFactor || 1;
            const cropW = Math.floor(vw / zoom);
            const cropH = Math.floor(vh / zoom);
            const cropX = Math.floor((vw - cropW) / 2);
            const cropY = Math.floor((vh - cropH) / 2);

            const tw = targetW;
            const th = Math.round(cropH * (tw / cropW));

            off.width = tw; off.height = th;
            const ctx = off.getContext("2d");
            ctx.drawImage(
                vid,
                cropX, cropY, cropW, cropH,  // 中央（ズーム）領域
                0, 0, tw, th                 // Python へ送信するためにリサイズ
            );

            off.toBlob((b) => resolve({ blob: b, size: { w: tw, h: th } }), "image/jpeg", 0.6);
        });

        const drawBestBox = (best, frameSize) => {
            const canvas = $("#canvas2"); const wrap = $("#video2");
            if (!canvas || !wrap || !best || !frameSize) {
                if (canvas) { const c2 = canvas.getContext("2d"); c2.clearRect(0, 0, canvas.width, canvas.height); }
                return;
            }
            const rect = wrap.getBoundingClientRect();
            canvas.width = rect.width; canvas.height = rect.height;
            const sx = canvas.width / frameSize.w, sy = canvas.height / frameSize.h;
            const ctx = canvas.getContext("2d"); ctx.clearRect(0, 0, canvas.width, canvas.height);

            // Tính toán countdown progress
            let color = "#00ff55";
            let label = "face";
            if (state.autoCyclePhase === "FACE" && !state.faceAutoCaptured && state.faceSeenSinceMs) {
                const now = Date.now();
                const total = state.faceAutoCaptureMs || 10000;
                const elapsed = now - state.faceSeenSinceMs;
                const ratio = Math.min(elapsed / total, 1);
                const remainingSec = Math.max(0, Math.ceil((total - elapsed) / 1000));

                color = `hsl(${Math.round(ratio * 120)}, 100%, 55%)`;
                label = `Capturing in ${remainingSec}s... ${Math.round(ratio * 100)}%`;
            }

            ctx.lineWidth = 4;
            ctx.strokeStyle = color;
            ctx.shadowColor = color;
            ctx.shadowBlur = 8;
            ctx.strokeRect(best.x * sx, best.y * sy, best.w * sx, best.h * sy);
 
            ctx.shadowBlur = 0;
            ctx.font = "bold 16px system-ui"; ctx.fillStyle = color;
            ctx.fillText(label, best.x * sx + 6, best.y * sy + 22);
        };
 
        const drawFrameWithBoxes = (imgBlob, frameSize) => {
            const canvas = $("#canvas2"); const wrap = $("#video2");
            if (!canvas || !wrap || !imgBlob) {
                if (canvas) { const c2 = canvas.getContext("2d"); c2.clearRect(0, 0, canvas.width, canvas.height); }
                return;
            }
            const rect = wrap.getBoundingClientRect();
            canvas.width = rect.width; canvas.height = rect.height;
            createImageBitmap(imgBlob).then((bitmap) => {
                const ctx = canvas.getContext("2d");
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                ctx.drawImage(bitmap, 0, 0, bitmap.width, bitmap.height, 0, 0, canvas.width, canvas.height);
 
                // サーバーが既にボックスを描画したフレームを返している場合でも、カウントダウンテキストを表示
                if (state.autoCyclePhase === "FACE" && !state.faceAutoCaptured && state.faceSeenSinceMs) {
                    const now = Date.now();
                    const total = state.faceAutoCaptureMs || 10000;
                    const elapsed = now - state.faceSeenSinceMs;
                    const remainingSec = Math.max(0, Math.ceil((total - elapsed) / 1000));
                    const ratio = Math.min(elapsed / total, 1);
                    const color = `hsl(${Math.round(ratio * 120)}, 100%, 55%)`;
 
                    ctx.font = "bold 20px system-ui";
                    ctx.fillStyle = color;
                    ctx.shadowColor = "black"; ctx.shadowBlur = 4;
                    ctx.fillText(`CAMERA READY: ${remainingSec}s`, 20, 40);
                    ctx.shadowBlur = 0;
                }
 
                bitmap.close();
            }).catch(() => { });
        };
 
        const frameDelay = Math.max(1, Math.round(1000 / state.presenceFps));
 
        while (state.presenceSending && vid.srcObject && myGen === state._presenceGeneration) {
 
            // 実行中に以下の状態になった場合:
            // 1) OCR が再開された、または
            // 2) すでに顔写真がある
            // → ループを停止し、検出の送信を中止
            if (!state.allowPresence) {
                log("allowPresence=false のため、Presence を停止しました。");
                break;
            }

            try {
                const { blob, size } = await getBlobAndSize();
                const fd = new FormData();
                fd.append("frame", blob, `frame-${Date.now()}.jpg`);
                fd.append("ts", String(Date.now()));
                const isFacePhase = state.autoCyclePhase === "FACE";
                const useFrameWithBoxes = isFacePhase && !!state.drawBoxesOnStream && !!state.presenceEndpointWithBoxes;
                const detectUrl = useFrameWithBoxes
                    ? state.presenceEndpointWithBoxes
                    : (isFacePhase ? state.faceEndpoint : state.presenceEndpoint);
                const res = await fetch(detectUrl, { method: "POST", body: fd });

                let currentFaceCount = 0;
                if (useFrameWithBoxes) {
                    if (res.ok && res.headers.get("Content-Type")?.includes("image")) {
                        const imgBlob = await res.blob();
                        const bestHeader = res.headers.get("X-Face-Best");
                        const sizeHeader = res.headers.get("X-Frame-Size");
                        state.lastPersonBox = bestHeader ? JSON.parse(bestHeader) : null;
                        state.lastFrameSize = sizeHeader ? JSON.parse(sizeHeader) : size;
                        currentFaceCount = state.lastPersonBox ? 1 : 0;
                        drawFrameWithBoxes(imgBlob, state.lastFrameSize);
                    } else {
                        state.lastPersonBox = null; state.lastFrameSize = null;
                        currentFaceCount = 0;
                        drawFrameWithBoxes(null, null);
                    }
                } else {
                    const js = await res.json().catch(() => null);
                    if (js && js.ok) {
                        state.lastPersonBox = js.best || null;
                        state.lastFrameSize = js.frame_size || size;
                        currentFaceCount = Array.isArray(js.boxes)
                            ? js.boxes.length
                            : (state.lastPersonBox ? 1 : 0);
                        drawBestBox(state.lastPersonBox, state.lastFrameSize);
                    } else {
                        state.lastPersonBox = null; state.lastFrameSize = null;
                        currentFaceCount = 0;
                        drawBestBox(null, null);
                    }
                }
                //123
                const hasFace = isFacePhase && !!state.lastPersonBox;

                // Auto-capture sau khi thấy face liên tục đủ faceAutoCaptureMs (Chỉ khi phase = FACE)
                if (!state.faceAutoCaptured && state.autoCyclePhase === "FACE") {
                    const nowMs = Date.now();
                    const readyAtMs = state.faceCaptureReadyAtMs || 0;
                    const delayMs = state.faceAutoCaptureMs ?? 1000;
                    if (nowMs < readyAtMs) {
                        state.faceSeenSinceMs = 0;
                    } else if (hasFace) {
                        if (!state.faceSeenSinceMs) state.faceSeenSinceMs = nowMs;
                        if (nowMs - state.faceSeenSinceMs >= delayMs) {
                            handleCaptureFace();
                            state.faceAutoCaptured = true;
                            // 撮影後にループを停止
                            state.presenceSending = false;
                        }
                    } else {
                        state.faceSeenSinceMs = 0;
                    }
                }

                const hasPerson = isFacePhase ? hasFace : !!state.lastPersonBox;
                const now = Date.now();
                if (currentFaceCount > state.presenceLastFaceCount) {
                    state.presenceFaceIncreaseStreak += 1;
                } else {
                    state.presenceFaceIncreaseStreak = 0;
                }

                if (hasPerson) {
                    clearWelcomeIdleTimer();

                    // Cập nhật vị trí nhìn cho Robot Avatar
                    if (state.welcomeEyesVisible && state.lastPersonBox) {
                        const box = state.lastPersonBox;
                        const centerX = (box.x + box.w / 2) / state.lastFrameSize.w;
                        const centerY = (box.y + box.h / 2) / state.lastFrameSize.h;
                        // Map tọa độ (vì Cam 2 thường là environment nên cần mirror dể trông tự nhiên nếu user đối diện)
                        state.avatar.target.x = centerX;
                        state.avatar.target.y = centerY;
                    }

                    if (state.welcomeEyesVisible) {
                        // 目を閉じてメインインターフェースに切り替える（この時点では音声は流さない）
                        showWelcomeEyes(false);
                        log("ユーザーを検出しました。名刺・顔撮影インターフェースに切り替えます。");
                    }
                } else {
                    // 顔写真の撮影リトライ中は、自動リセットをブロックします。
                    // これにより、（案内音声の再生中であっても）15回のリトライが中断されずに実行可能になります。
                    if (state.autoCyclePhase === "FACE" && state.faceRetryCount < state.faceRetryMaxCount) {
                        clearWelcomeIdleTimer();
                    } else {
                        scheduleWelcomeIdleReturn();
                    }
                }

                // 挨拶 (Greeting) - IDLE 状態（待機中）のみ挨拶
                const hasNewPerson = (hasPerson && !state.presenceLastHadPerson) ||
                    (hasPerson &&
                        currentFaceCount > state.presenceLastFaceCount &&
                        state.presenceFaceIncreaseStreak >= (state.presenceStableIncreaseFrames || 2));

                // 追加条件: 名刺処理中や顔撮影中（REMOVING など）は挨拶しない
                if (hasNewPerson && !state.welcomeEyesVisible && !state.suppressGreetingDuringCompletion && state.autoCyclePhase === "IDLE") {
                    if (now - state.presenceLastGreetTs > state.presenceGreetCooldownMs) {
                        playQueuedAudio(sndGreet);
                        setTimeout(() => playQueuedAudio(sndCardQrGuide), 1000);
                        state.presenceLastGreetTs = now;
                        state.presenceFaceIncreaseStreak = 0;
                    }
                }

                // 2. 案内 (Instruction) - ユーザーが3秒間立ち止まっている場合
                if (hasPerson) {
                    // 初めて顔を検出した場合、現在時刻を記録
                    if (!state.faceSeenSinceMs) state.faceSeenSinceMs = now;

                    const seenDuration = now - state.faceSeenSinceMs;

                } else {
                    // ユーザーがいない場合、タイムスタンプをリセット
                    state.faceSeenSinceMs = 0;
                }

                state.presenceLastHadPerson = hasPerson;
                state.presenceLastFaceCount = currentFaceCount;

            } catch (err) {
                log("Presence エラー: " + (err?.message || err));
            }
            await new Promise(r => setTimeout(r, frameDelay));
        }

        state.presenceSending = false;
        // 停止時にフレームをクリア
        const canvas = $("#canvas2");
        if (canvas) {
            const c2 = canvas.getContext("2d");
            c2.clearRect(0, 0, canvas.width, canvas.height);
        }
    }

    const btnCam2Start = $("#cam2-start"); if (btnCam2Start) btnCam2Start.addEventListener("click", startCam2);
    const btnCam2Stop = $("#cam2-stop"); if (btnCam2Stop) btnCam2Stop.addEventListener("click", stopCam2);

    async function handleCaptureFace() {
        const vid = $("#video2");
        const zoomCanvas = document.getElementById("video2-zoom");

        // Camera + canvas zoom phải sẵn sàng
        if (!vid || !vid.srcObject || !zoomCanvas || !zoomCanvas.width) {
            log("カメラ 2 / ズームフレームが準備できていません");
            return;
        }
 
        // 青い枠内に顔があることを確認するため、顔検出の bbox を使用
        const best = state.lastPersonBox;
        const fsz = state.lastFrameSize;
        if (!best || !fsz) {
            log("顔が検出されていません — 青い枠内に顔を入れてください。");
            return;
        }

        // 自動サイクルの途中の場合、フェーズを SUBMITTING に変更
        if (state.autoCyclePhase === "FACE") {
            state.autoCyclePhase = "SUBMITTING";
            if (state.faceRetryTimer) {
                clearTimeout(state.faceRetryTimer);
                state.faceRetryTimer = null;
            }
            state.faceRetryCount = 0;
        }

        // CPU がビジーになる前にレンダリングタスクを優先するため、requestAnimationFrame を使用
        requestAnimationFrame(async () => {
            const off = document.createElement("canvas");
            off.width = zoomCanvas.width;
            off.height = zoomCanvas.height;
            const ctx = off.getContext("2d");
            ctx.drawImage(zoomCanvas, 0, 0, off.width, off.height);
 
            log(`顔写真を処理中 (${off.width}x${off.height})...`);
 
            // メインスレッドをブロックしないよう、非同期の toBlob を使用
            off.toBlob((blob) => {
                if (!blob) return;
 
                // 1. Blob URL を使用して即座にプレビューを表示 (非常に高速)
                const previewUrl = URL.createObjectURL(blob);
                const facePrev = $("#face-preview");
                if (facePrev) {
                    // メモリリークを防ぐため、古い URL を解放
                    if (facePrev._blobUrl) URL.revokeObjectURL(facePrev._blobUrl);
                    facePrev._blobUrl = previewUrl;
                    facePrev.src = previewUrl;
                    facePrev.style.maxWidth = "100%";
                    facePrev.style.transform = "none";
                }
 
                // 2. サーバー送信用の Base64 変換をバックグラウンドで実行
                const reader = new FileReader();
                reader.onloadend = () => {
                    state.faceDataUrl = reader.result;
 
                    state.presenceFps = 1;
                    log(`顔写真の撮影が完了しました (Base64 準備完了)。`);
                    updateActionButtons();
                    tryFinalizeSessionAfterFaceAndOcr("face-captured");
                };
                reader.readAsDataURL(blob);
            }, "image/jpeg", 0.8);
        });
    }
    const btnCapture = $("#capture-face");
    if (btnCapture) btnCapture.addEventListener("click", handleCaptureFace);

    async function resetAll(soft = false) {
        clearWelcomeIdleTimer();
        interruptAndClearAudioQueue(); // 問題 2: 再生中の音声をクリア
        if (state.thankYouResetTimer) {
            clearTimeout(state.thankYouResetTimer);
            state.thankYouResetTimer = null;
        }
        // 前回の resetAll 呼び出しによる未完了の再起動タイマーをキャンセル
        if (state._resetRestartTimer) {
            clearTimeout(state._resetRestartTimer);
            state._resetRestartTimer = null;
        }
        stopBcardOcrPolling();

        // 1. Dừng presence + auto-card + camera
        try {
            if (!soft) {
                state._presenceGeneration++;    // 古い presence ループを強制終了
                state.presenceSending = false;  // presence 内の while ループを停止
                state.cardAutoSending = false;
                await stopCam1();
                await stopCam2();
            }
            state.cardAutoDone = false;
        } catch (err) {
            log("リセット中のカメラ停止エラー: " + (err?.message || err));
        }

        // 2. canvas2 上の認識枠を消去
        const canvas2 = $("#canvas2");
        if (canvas2) {
            const c2 = canvas2.getContext("2d");
            c2.clearRect(0, 0, canvas2.width, canvas2.height);
        }

        // 3. 状態ロジックのリセット
        state.faceDataUrl = null;
        state.serverQRUrl = null;
        state.bcardImageDataUrl = null;
        state.lastBCardFields = null;
        state.lastBCardText = null;
        state.registrationId = null;
        state.lastQRRaw = null;
        state.allowPresence = true;
        state.autoCyclePhase = "IDLE"; // 待機状態にリセット
        state.faceAutoCaptured = false;
        state.faceCaptureReadyAtMs = 0;
        state.emptyGapCount = 0;
        state.cardRetryCount = 0;
        state.presenceLastHadPerson = false;
        state.presenceLastFaceCount = 0;
        state.presenceFaceIncreaseStreak = 0;
        state.suppressGreetingDuringCompletion = false;
        state.ocrTaskId = null;
        state.ocrStatus = "idle";
        state.sessionFinalizeTriggered = false;
        state.cardRetryRequested = false;
        if (state.faceGuideAudioTimer) { clearTimeout(state.faceGuideAudioTimer); state.faceGuideAudioTimer = null; }
        if (state.faceRetryTimer) { clearTimeout(state.faceRetryTimer); state.faceRetryTimer = null; }
        state.faceRetryCount = 0;
        showRemoveCardOverlay(false);
        state.faceSeenSinceMs = 0;
        showWelcomeEyes(true);
 
        // 4. フォーム情報の消去
        ["#fullName", "#idNumber", "#oldId", "#dob", "#gender", "#expiry", "#address", "#issued"]
            .forEach(sel => setVal(sel, ""));

        fillBCardPane({}, "");
        toggleBCardPane(false);
        toggleCCCDForm(true);

        const facePrev = $("#face-preview");
        if (facePrev) facePrev.src = "";

        const qrImg = $("#qr-from-server");
        if (qrImg) qrImg.src = "";

        // 5. UI 更新
        if (!soft) {
            window.cam1Started = false;
            const p1 = $("#cam1-power"); if (p1) p1.classList.remove("on");
            const p2 = $("#cam2-power"); if (p2) p2.classList.remove("on");
        }
 
        uiSetScanLockedCam1(false);
        uiSetScanLockedCam2(false);
        state.cardAutoDone = false; // 新しい名刺のスキャンを常に許可
        updateActionButtons();
        log(soft ? "システムを IDLE 状態にソフトリセットしました（カメラは維持）。" : "システムを IDLE 状態にリセットしました。");
 
        if (soft) {
            // 6. インテリジェンスループが停止している場合は再開を確認
            if (!state.presenceSending && state.allowPresence) {
                startPresenceStream();
            }
            if (!state.cardAutoSending) {
                startAutoCardFromCam1();
            }
            return;
        }
 
        // 6. 2秒後に両方のカメラを再起動（resetAll が再度呼ばれた場合にキャンセルできるようタイマー ID を保存）
        state._resetRestartTimer = setTimeout(async () => {
            state._resetRestartTimer = null;
            try {
                // カメラ 1 がオフの場合は再起動
                if (!window.cam1Started) {
                    await startCam1();
                }
            } catch (err) {
                log("リセット後にカメラ 1 を再起動できませんでした: " + (err?.message || err));
            }
 
            try {
                // カメラ 2 がオフの場合は再起動
                const v2 = $("#video2");
                const isOn = v2 && v2.srcObject;
                if (!isOn) {
                    await startCam2();
                }
            } catch (err) {
                log("リセット後にカメラ 2 を再起動できませんでした: " + (err?.message || err));
            }
        }, 2000);
    }

    // === フルスクリーン終了 / ウェブアプリ閉鎖 / リセットの処理 ===
    function createSystemButtons() {
        // --- 1. 終了ボタン ---
        const btnExit = document.createElement("button");
        btnExit.innerText = "終了";
        Object.assign(btnExit.style, {
            position: "fixed",
            bottom: "10px",
            right: "10px",
            padding: "5px 10px",
            backgroundColor: "rgba(200, 200, 200, 0.3)",
            color: "rgba(100, 100, 100, 0.5)",
            border: "none",
            borderRadius: "4px",
            fontSize: "12px",
            zIndex: "9999",
            cursor: "pointer"
        });

        btnExit.addEventListener('click', function (e) {
            e.preventDefault();
            log("アプリケーションを閉じています...");
            window.close();
        });

        document.body.appendChild(btnExit);

        // --- 2. 一括リセットボタン ---
        const btnResetManual = document.createElement("button");
        btnResetManual.innerText = "リセット";
        Object.assign(btnResetManual.style, {
            position: "fixed",
            bottom: "10px",
            right: "60px", // Nhích sang trái so với nút thoát
            padding: "5px 10px",
            backgroundColor: "rgba(200, 200, 200, 0.3)",
            color: "rgba(100, 100, 100, 0.5)",
            border: "none",
            borderRadius: "4px",
            fontSize: "12px",
            zIndex: "9999",
            cursor: "pointer"
        });

        btnResetManual.addEventListener('click', function (e) {
            e.preventDefault();
            log("システムを手動でリセットしました...");
            resetAll();
        });

        document.body.appendChild(btnResetManual);
    }
    createSystemButtons();

    const btnReset = $("#btn-reset"); if (btnReset) btnReset.addEventListener("click", resetAll);

    async function handleRegister(e) {
        if (e) e.preventDefault();
        const readyForRegister = (hasCccdData() || hasBcardData()) && !state.isSubmitting;
        if (!readyForRegister) {
            log("登録に必要なデータが不足しているか、送信中です。");
            return;
        }
        await sendPayloadToPython("register");
    }
    const btnRegister = $("#btn-register");
    if (btnRegister) btnRegister.addEventListener("click", handleRegister);

    const btnPrint = $("#btn-print-qr");
    if (btnPrint) btnPrint.addEventListener("click", (e) => {
        if (!state.serverQRUrl) {
            e.preventDefault();
            alert("まだQRコードがありません。先に「登録する」を押してください。");
            return;
        }
        const src = ($("#qr-from-server") && $("#qr-from-server").src) || state.serverQRUrl;
        if (!src) { alert("まだQRコードがありません。先に「登録する」を押してください。"); return; }

        const abs = src.startsWith("http") ? src : new URL(src, window.location.origin).toString();
        const url = abs + (abs.includes("?") ? "&" : "?") + "t=" + Date.now();

        const iframe = document.createElement("iframe");
        iframe.style.position = "fixed";
        iframe.style.right = "0"; iframe.style.bottom = "0";
        iframe.style.width = "0"; iframe.style.height = "0";
        iframe.style.border = "0";
        document.body.appendChild(iframe);

        const html = `
      <!doctype html>
      <html>
        <head>
          <meta charset="utf-8" />
          <title>QR登録</title>
          <base href="${window.location.origin}/">
          <style>
            @page { size:auto; margin:10mm; }
            html, body { height:100%; margin:0; background:#fff; }
            body { display:flex; align-items:center; justify-content:center; }
            img  { width:45mm; height:45mm; object-fit:contain; }
          </style>
        </head>
        <body>
          <img id="qr" src="${url}" alt="QR" />
          <script>
            const img = document.getElementById('qr');
            img.onload = () => {
              window.focus(); window.print();
              setTimeout(() => { parent.document.body.removeChild(frameElement); }, 300);
            };
          <\/script>
        </body>
      </html>`;
        const doc = iframe.contentWindow.document;
        doc.open(); doc.write(html); doc.close();
    });

    function ensurePowerButtonForVideo(videoSel, btnId, title = "Power", icon = "⏻") {
        const v = document.querySelector(videoSel);
        if (!v) return null;
        const wrap = v.closest('.video-wrap') || v.parentElement;
        if (!wrap) return null;

        let btn = document.getElementById(btnId);
        if (!btn) {
            btn = document.createElement('button');
            btn.id = btnId;
            btn.className = 'power-btn';
            btn.title = title;
            btn.type = 'button';
            btn.innerHTML = `
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
             width="26" height="26" aria-hidden="true">
          <path d="M12 3 v7"
                stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/>
          <path d="M7.5 6.5
                  a7 7 0 1 0 9 0"
                stroke="currentColor" stroke-width="2" stroke-linecap="round"
                fill="none"/>
        </svg>`;
            wrap.appendChild(btn);
        }
        return btn;
    }

    const cam1Power = ensurePowerButtonForVideo('#video1', 'cam1-power', 'Camera 1');
    const cam2Power = ensurePowerButtonForVideo('#video2', 'cam2-power', 'Camera 2');

    if (cam1Power) cam1Power.addEventListener('click', async () => {
        try {
            if (!window.cam1Started) {
                const btn = document.querySelector('#cam1-start');
                if (btn) btn.click(); else await startCam1();
                cam1Power.classList.add('on');
            } else {
                const btn = document.querySelector('#cam1-stop');
                if (btn) btn.click(); else await stopCam1();
                cam1Power.classList.remove('on');
            }
        } catch { }
    });

    if (cam2Power) cam2Power.addEventListener('click', async () => {
        try {
            const v2 = document.querySelector('#video2');
            const isOn = v2 && v2.srcObject;
            if (!isOn) {
                const btn = document.querySelector('#cam2-start');
                if (btn) btn.click(); else await startCam2();
                cam2Power.classList.add('on');
            } else {
                const btn = document.querySelector('#cam2-stop');
                if (btn) btn.click(); else await stopCam2();
                cam2Power.classList.remove('on');
            }
        } catch { }
    });

    // Nút điều khiển cửa sổ (fullscreen browser: Chrome/Firefox)
    (function setupWindowButton() {
        const btn = document.createElement('button');
        btn.id = 'win-ctl-btn';
        btn.type = 'button';
        btn.title = '全画面表示 / 解除 (F11 でも可)';
        btn.className = 'floating-win-btn';
        btn.textContent = '⛶';

        document.body.appendChild(btn);

        // ---- Helpers Fullscreen ----
        function isFullscreen() {
            return !!(
                document.fullscreenElement ||
                document.webkitFullscreenElement ||
                document.mozFullScreenElement ||
                document.msFullscreenElement
            );
        }

        function requestFs(elem) {
            const el = elem || document.documentElement;
            if (el.requestFullscreen) return el.requestFullscreen();
            if (el.webkitRequestFullscreen) return el.webkitRequestFullscreen();
            if (el.mozRequestFullScreen) return el.mozRequestFullScreen();
            if (el.msRequestFullscreen) return el.msRequestFullscreen();
            return Promise.resolve();
        }

        function exitFs() {
            if (document.exitFullscreen) return document.exitFullscreen();
            if (document.webkitExitFullscreen) return document.webkitExitFullscreen();
            if (document.mozCancelFullScreen) return document.mozCancelFullScreen();
            if (document.msExitFullscreen) return document.msExitFullscreen();
            return Promise.resolve();
        }

        async function toggleFullscreen() {
            try {
                if (!isFullscreen()) {
                    await requestFs(document.documentElement);
                } else {
                    await exitFs();
                }
            } catch (err) {
                console.error('Fullscreen error:', err);
            }
        }

        // Cập nhật trạng thái nút khi fullscreen thay đổi
        function onFsChange() {
            if (isFullscreen()) {
                btn.classList.add('is-fullscreen');
                btn.title = '全画面解除 (F11 または再クリック)';
            } else {
                btn.classList.remove('is-fullscreen');
                btn.title = '全画面表示 (F11 または ⛶ ボタン)';
            }
        }

        document.addEventListener('fullscreenchange', onFsChange);
        document.addEventListener('webkitfullscreenchange', onFsChange);
        document.addEventListener('mozfullscreenchange', onFsChange);
        document.addEventListener('MSFullscreenChange', onFsChange);

        // Click nút: bật/tắt fullscreen
        btn.addEventListener('click', async (e) => {
            e.preventDefault();
            await toggleFullscreen();
        });

        // Chuột phải: chỉ thoát fullscreen (nếu đang full)
        btn.addEventListener('contextmenu', async (e) => {
            e.preventDefault();
            if (isFullscreen()) {
                await exitFs();
            }
        });

        // F11: chặn mặc định, dùng Fullscreen API cho đồng nhất
        document.addEventListener('keydown', async (e) => {
            if (e.key === 'F11' || e.keyCode === 122) {
                e.preventDefault();
                await toggleFullscreen();
            }
        });
    })();

    // ====== INIT ======
    (async () => {
        await populateCameras();
        updateActionButtons();

        // Init robot avatar
        const eyeL = document.querySelector(".welcome-eye.left");
        if (eyeL) {
            computeAvatarMetrics();
            new ResizeObserver(computeAvatarMetrics).observe(eyeL);
            window.addEventListener("resize", computeAvatarMetrics);
            randomBlink();
            tickAvatar();
        }

        showWelcomeEyes(true);
        clearWelcomeIdleTimer();

        // カメラの自動起動
        try {
            await startCam1();
            await startCam2();
            log("システムがカメラを自動起動しています...");
        } catch (err) {
            log("カメラの自動起動エラー: " + err.message);
        }
 
        log("準備完了。自動スキャンおよび登録システムが起動しました。");
    })();

})();
