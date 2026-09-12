/*
 * Actual's web client is built for the site root. Ingress serves it from a
 * path instead, and that path is only known once a request arrives, so it
 * cannot be baked into the bundle. What is known to the browser is the base
 * the document was served under, which NGINX writes into the page on the way
 * past, and this reads it back so the client can resolve its own addresses
 * against it.
 *
 * Loaded as a file rather than written inline because the server sends a
 * content security policy of "script-src 'self'", which an inline script would
 * fall foul of.
 */
window.__actualBasePath = new URL(".", document.baseURI).pathname;

/*
 * Actual keeps its budget in SQLite compiled to WebAssembly, and reads it
 * through a SharedArrayBuffer. Browsers only hand one out to a page that is
 * cross-origin isolated, which a page needs its whole frame chain to ask for.
 * Under Ingress the outer page is Home Assistant's, which does not, so the
 * SharedArrayBuffer is never there to be had and no header this app sends can
 * change that.
 *
 * Actual has a fallback for exactly this, behind a confirmation that stops on
 * a page of explanation. Reaching it is the only way in through the sidebar,
 * so the answer is given here rather than asked for. It costs the ability to
 * have the budget open in two tabs at once: the second tab takes the lock and
 * the first stops saving, and says so. Direct access over SSL is unaffected
 * and runs the supported path.
 */
if (window.__actualBasePath !== "/") {
  try {
    window.localStorage.setItem("SharedArrayBufferOverride", "true");
  } catch (err) {
    /* No storage to write to; Actual then asks the question itself. */
  }
}
