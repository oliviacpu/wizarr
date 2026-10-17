(() => {
    const script = document.currentScript;
    const stateKey = "wizarr.accessRequestSignIn";
    const errorBox = document.getElementById("request-error");
    const cancelButton = document.getElementById("cancel-request-signin");
    let signInAttempt = 0;
    const csrf = () => document.querySelector('[name="csrf_token"]')?.value || "";
    const showError = () => {
        errorBox.textContent = "Sign-in or setup could not be completed. Refresh status before trying again.";
    };
    function showStep(id) {
        const panel = document.getElementById(id);
        if (!panel) return;
        for (const step of document.querySelectorAll("[data-request-panel]")) {
            step.hidden = step !== panel;
        }
        (panel.querySelector("input:not([type=hidden])") || panel.querySelector("h2"))?.focus();
    }
    function clearSignIn() {
        signInAttempt += 1;
        sessionStorage.removeItem(stateKey);
        if (cancelButton) cancelButton.hidden = true;
        for (const form of document.querySelectorAll("[data-oauth-mode]")) {
            form.querySelector('[type="submit"]').disabled = false;
        }
    }
    for (const button of document.querySelectorAll("[data-request-step]")) {
        button.addEventListener("click", () => {
            clearSignIn();
            errorBox.textContent = "";
            showStep(button.dataset.requestStep);
        });
    }
    cancelButton?.addEventListener("click", () => {
        clearSignIn();
        errorBox.textContent = "";
        showStep("request-details");
    });
    async function post(url, data) {
        const response = await fetch(url, {method: "POST", body: data, credentials: "same-origin"});
        const result = await response.json();
        if (!response.ok) {
            errorBox.textContent = result.error || "Please refresh and try again.";
            return null;
        }
        return result;
    }
    async function claimLink() {
        const token = location.hash.slice(1);
        if (!/^[A-Za-z0-9_-]{43}$/.test(token)) return;
        const link = location.href;
        history.replaceState(null, "", location.pathname);
        const data = new FormData();
        data.set("csrf_token", csrf());
        data.set("token", token);
        const result = await post(script.dataset.claimUrl, data);
        if (!result) return;
        // Keep the capability in the private page only, never in web storage.
        if (document.querySelector("[data-request-id]")?.dataset.requestId !== String(result.request_id)) {
            history.replaceState(null, "", result.redirect + "#" + token);
            location.reload();
            return;
        }
        document.getElementById("private-link").href = link;
        document.getElementById("private-link-container").hidden = false;
    }
    async function finishSignIn() {
        const saved = sessionStorage.getItem(stateKey);
        if (!saved) return;
        const state = JSON.parse(saved);
        const form = document.querySelector(`[data-oauth-mode="${state.mode}"]`);
        if (!form) return;
        for (const name of ["identifier", "display_name"]) {
            if (form.elements[name]) form.elements[name].value = state[name] || "";
        }
        if (state.mode === "submit") showStep("request-details");
        if (Date.now() > state.deadline) {
            clearSignIn();
            showError();
            return;
        }
        const attempt = ++signInAttempt;
        form.querySelector('[type="submit"]').disabled = true;
        if (cancelButton) cancelButton.hidden = false;
        errorBox.textContent = "Confirming Plex sign-in…";
        try {
            while (Date.now() <= state.deadline) {
                const token = await plexOAuthPollToken(state);
                if (attempt !== signInAttempt) return;
                if (token) {
                    sessionStorage.removeItem(stateKey);
                    if (cancelButton) cancelButton.hidden = true;
                    const data = new FormData(form);
                    data.set("oauth_token", token);
                    const result = await post(form.action, data);
                    if (attempt !== signInAttempt) return;
                    clearSignIn();
                    if (result) location.replace(result.redirect);
                    return;
                }
                await new Promise(resolve => setTimeout(resolve, 1500));
            }
        } catch (error) {
            if (attempt === signInAttempt) throw error;
            return;
        }
        if (attempt === signInAttempt) {
            clearSignIn();
            showError();
        }
    }
    for (const form of document.querySelectorAll("[data-oauth-mode]")) {
        form.addEventListener("submit", async event => {
            event.preventDefault();
            clearSignIn();
            const attempt = signInAttempt;
            form.querySelector('[type="submit"]').disabled = true;
            if (cancelButton) cancelButton.hidden = false;
            try {
                const clientId = crypto.randomUUID();
                const response = await fetch("https://plex.tv/api/v2/pins?strong=true", {
                    method: "POST",
                    headers: {Accept: "application/json", "X-Plex-Client-Identifier": clientId, "X-Plex-Product": "Wizarr"},
                });
                if (!response.ok) throw new Error("Plex sign-in unavailable");
                const pin = await response.json();
                if (attempt !== signInAttempt) return;
                const state = {
                    pinId: pin.id, clientId, mode: form.dataset.oauthMode,
                    identifier: form.elements.identifier?.value || "",
                    display_name: form.elements.display_name?.value || "",
                    deadline: Date.now() + 5 * 60 * 1000,
                };
                sessionStorage.setItem(stateKey, JSON.stringify(state));
                const params = new URLSearchParams({
                    clientID: clientId, code: pin.code, "context[device][product]": "Wizarr",
                    forwardUrl: location.origin + location.pathname,
                });
                location.assign("https://app.plex.tv/auth/#!?" + params);
            } catch {
                if (attempt === signInAttempt) {
                    clearSignIn();
                    showError();
                }
            }
        });
    }
    claimLink().then(finishSignIn).catch(() => {
        clearSignIn();
        showError();
    });
})();
