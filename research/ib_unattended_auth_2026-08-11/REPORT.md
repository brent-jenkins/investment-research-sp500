# Interactive Brokers Web API — unattended authentication for an individual account (2026-08-11)

**Verdict: NOT feasible via any documented route. Per IB's own authentication documentation,
Individual Accounts are eligible for the Client Portal Gateway (session-based username/password) ONLY;
OAuth 1.0a and OAuth 2.0 are both listed as available to organisation and third-party account types,
and Individual Accounts are absent from both lists.** Session auth requires an interactive login at
least weekly, which on this account sends an SMS code, which cannot be legitimately automated.

**One specific question could overturn this, and it cannot be settled by reading**: multiple community
sources report that individual accounts *can* in practice register OAuth 1.0a self-service, explicitly
noting this contradicts IB's website. That is a question for IB support, not for more searching — see
"The one open question" below.

Investigation only. No code, no credentials, no connection attempted, nothing deployed.

---

## The crux, answered

You suspected the "Trading Access for Individuals" page's mention of "an IBKR username and password"
indicated session-based auth rather than OAuth. **That reading is correct**, and IB's Authentication
documentation states it structurally rather than by implication:

| Method | Account types listed as eligible |
|---|---|
| **Client Portal Gateway** (session, username/password) | **Individual Accounts** |
| **OAuth 1.0a** | Advisor · Broker & FCM · Proprietary Trading Group · Hedge & Mutual Fund · Institutional Hedge Fund Investors · Third Party Software Developers |
| **OAuth 2.0** | The same organisational/institutional set |

**Individual Accounts appear against the gateway and nowhere else.** IB further states *"OAuth 1.0a is
the only supported authentication method for Third Party Developers"* — framing OAuth throughout as
the vendor/institution path, not the retail one. Supporting material is consistent: *"For retail and
individual clients, authentication to the WebAPI is managed using the Client Portal Gateway"*, and
*"Individual clients using the CP Gateway tool must complete a manual login with their IBKR username
and password."*

**That manual login is the whole problem.** It is not avoidable by configuration, and it is exactly
the event that triggers the SMS challenge.

---

## The six questions

**1. Which auth methods can individuals actually use?** Session-based via Client Portal Gateway only,
per the eligibility table above. **OAuth is documented as organisation-only** — which, as you
anticipated, likely ends this.

**2. What does "approval or configuration prior to use" involve, and can an individual complete it?**
The phrase attaches to the OAuth methods, not to the gateway. For third-party vendors it means genuine
**Compliance approval**, initiated by an onboarding form to `webapionboarding@interactivebrokers.com`;
IB defines third-party developers as vendors interacting with client accounts *"to which the vendor has
no formal relationship"* — which does not describe someone automating their own account. For
first-party organisational use it means **configuration**: the OAuth self-service portal (RSA signing
and encryption key pairs, a Diffie-Hellman prime, a 9-character consumer key, then token generation).
Whether an *individual* can complete that portal flow is precisely the disputed point in "The one open
question" below. By contrast, the gateway needs no approval at all: *"Clients with fully open and
funded accounts may use our Web API's Trading features to trade their accounts immediately, without any
onboarding or approval process."*

**3. Token lifetime and refresh — does re-authorisation eventually require interactive login?**

*Under session auth (the documented individual path):* **Yes, and this is fatal.** The gateway's
brokerage session is kept alive by `/tickle`, and TWS/Gateway can auto-restart daily unattended — but
**security tokens are invalidated every Sunday at 01:00 ET**, after which you *"must manually
authenticate... the first time you log into the platform after the security tokens have been
invalidated."* Ceiling: **~6.5 days unattended, then a human**.

*Under OAuth 1.0a (if it were available):* it would be genuinely indefinite. A **permanent access
token** from setup; a **live session token valid ~24h** derived programmatically via an RSA-SHA256
signed request to `/oauth/live_session_token`, a Diffie-Hellman exchange and HMAC-SHA1 derivation;
brokerage session initialised with `POST /iserver/auth/ssodh/init` and re-callable on disconnect. IB's
own campus material describes this as automatable — *"you can even automate the way to reauthenticate
every day."* No interactive login, no 2FA: the RSA private key **is** the factor. This is what makes
the eligibility question decisive rather than academic.

**4. Is the full Trading surface available, or a read-only subset?** Full, under either auth method —
this is not a limitation. Order placement, positions and account state are all Trading endpoints.
**OAuth 1.0a covers trading features only**; IB's separate *Account Management* API (onboarding,
funding, statements) needs OAuth 2.0 — irrelevant here, since this system uses none of it.

**5. Pacing — could IB serve as a price source?** Technically yes, comfortably. **50 requests/second
per authenticated username** on the direct Web API, but only **10 req/s via the CP Gateway** — the
individual path is 5× slower. Endpoint caps matter more than the global rate:
`/iserver/marketdata/history` allows **5 concurrent requests**. Breaching returns **429**, with a
**10-minute IP penalty box** and permanent blocking for repeat offenders. For ~500 tickers nightly
that is workable.

**But the real objection is not capacity.** Switching the price source from yfinance to IB would
invalidate every backtest figure this project holds — the 19-year history, the ~1.08pp noise floor, the
16-block baseline, Arm B's +1.993pp are all computed on yfinance `close`. IB as an *execution venue* is
a contained change; IB as a *price source* silently re-bases every number the system has ever been
validated against. Those are separate decisions and should not be bundled.

**6. What funding threshold qualifies as "funded"?** **The documentation does not state a number.** It
says only that the live account must be *"fully open and funded"* and of *"IBKR Pro"* type, and that
this applies *"whether accessing a live account or its associated simulated paper account"* — so paper
access rides on the live account's eligibility, as you already established. **This needs confirming
with IB support directly rather than guessed at.** Note the account must also be **IBKR Pro**, not IBKR
Lite — worth verifying the existing live account's type at the same time.

---

## The one open question — and why I am not resolving it by reading

Community sources directly contradict IB's documentation. The most specific, from the `ibind` project's
OAuth guide:

> *"Despite the website and some IBKR support agents claiming otherwise, indeed it seems to be possible
> to use OAuth 1.0a on individual accounts"* — adding that individual users have registered **both live
> and paper** credentials, and that for paper you *"simply login using your paper credentials when
> accessing the Client Portal"* and repeat the same process.

**I am not recommending acting on that, for three reasons.** It contradicts IB's own eligibility list,
which is the document IB would point to. Undocumented access paths can be withdrawn without notice or
deprecation, and this one would sit under the system's order placement. And reports mention activation
delays of *up to two weeks*, which makes a fast empirical test awkward.

**But it is also the single thing that would flip this verdict**, so it deserves a direct answer rather
than an assumption in either direction. **The action is one support ticket to IB**, asking specifically:

> *Can an Individual IBKR Pro account register OAuth 1.0a credentials via the OAuth self-service portal
> for first-party access to its own account and associated paper account — and if so, is that a
> supported configuration or a tolerated one?*

Ask about the funding threshold (Q6) in the same ticket. Both are facts IB holds and neither is
discoverable from outside.

**A note on my own earlier reasoning, since it bears on how much weight to give the community claim:**
my first pass through this leaned on the `ibind` guide and concluded OAuth was available to
individuals. Reading IB's actual Authentication documentation reversed that. The community sources are
not worthless — they describe people's real experience — but "several practitioners report it works"
and "the vendor documents it as available for your account type" are different classes of evidence,
and only the second is a sound basis for an authentication mechanism underneath live order placement.

---

## Terms of service

Nothing examined here requires circumventing 2FA, and the distinction is worth stating precisely:
using OAuth would be **using IB's own sanctioned key-pair credential as designed** — the SMS challenge
is never reached because the RSA key is the factor. That is categorically different from automating a
response to an SMS challenge, which would be circumvention.

The approach to avoid is the third-party tooling that drives the gateway's login form to inject a 2FA
code. Its own maintainers describe it as *"not using an officially supported automatic interface"*,
offered *"on a best-effort basis with no guarantee... [and risk of] account lock-outs."* Unsupported,
fragile, and carrying lock-out risk on the account that would be placing orders.

For completeness on the fallback: **IBC cannot get past an SMS challenge** — correct as you believed.
It supports only IBKR Mobile (IB Key) push 2FA, and even then *"you must manually acknowledge the 2FA
alert on your mobile device."* Its `ReloginAfterSecondFactorAuthenticationTimeout` setting only retries
when the human does not respond within ~3 minutes. It automates everything *around* the human step
without removing it. Nor is there relief in reducing 2FA: it is **mandatory on all IB accounts
including paper**, IB *"does not allow the ability to disable the Secure Login System"*, and the old
second-user-without-2FA workaround has been withdrawn.

---

## Recommendation

**Treat unattended IB operation as closed for now, pending one support answer.** Do not scope the
integration — the instruction was to scope only if unattended operation is feasible, and on the
documented evidence it is not.

The documented individual path caps at **~6.5 days unattended with a mandatory human SMS login every
Sunday**. That is a poor fit for this system specifically: its premise is removing discretionary human
involvement from a daily automated cycle, and the failure mode is silent — a missed Sunday login means
the system simply stops trading, with no error, which is the failure shape this project is least
tolerant of.

**Funding is not the blocker.** You have an IBKR Pro live account and funding available, and the
eligibility gate is real but surmountable. **The blocker is that funding alone does not grant OAuth to
an Individual account** — so funding it now, on the assumption that unattended access follows, would be
paying to discover the answer to a question a support ticket answers for free. Get the answer first.

If IB confirms individual OAuth is supported, this reopens immediately and favourably: the technical
mechanism is genuinely sound, indefinitely refreshable, gateway-free and ToS-clean, and scoping the
integration becomes the sensible next task. If IB confirms it is not, the question is closed properly
rather than left as a perpetual maybe — and the sleeve study stays shelved for want of a broker, which
is a real cost but a known one.

---

## Sources

- [IBKR — Web API Authentication: Introduction](https://ibkrcampus.com/docs/web-api/authentication/introduction) *(the eligibility table; the decisive source)*
- [IBKR — Trading Web API](https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/)
- [IBKR — Web API Trading: Introduction](https://ibkrcampus.com/docs/web-api/trading/getting-started/introduction)
- [IBKR — Pacing Limitations](https://www.interactivebrokers.com/docs/web-api/trading/usage-and-availability/pacing-limitations)
- [IBKR — Auto Restart Considerations](https://www.ibkrguides.com/traderworkstation/auto-restart-considerations.htm)
- [IBKR — Secure Login System](https://www.interactivebrokers.com/en/general/secure-login.php)
- [IBKR — Secure Login System FAQ](https://ibkrguides.com/securelogin/sls/faq.htm)
- [IBKR — Authenticating with the Client Portal REST API](https://www.interactivebrokers.com/campus/traders-insight/authenticating-with-the-ibkr-client-portal-rest-api/)
- [IBKR — Web API Changelog](https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-changelog/)
- [IBC user guide](https://github.com/IbcAlpha/IBC/blob/master/userguide.md)
- [ibind wiki — OAuth 1.0a](https://github.com/Voyz/ibind/wiki/OAuth-1.0a) *(the contradicting community claim)*
- [IBeam wiki — Two Factor Authentication](https://github.com/Voyz/ibeam/wiki/Two-Factor-Authentication)
- [IBKR OAuth 1.0a walkthrough (marchenko1985)](https://marchenko1985.github.io/ibkr-api-oauth/)

**Source caveat:** most `interactivebrokers.com/docs` and `/campus` pages return HTTP 403 to automated
fetching. The decisive eligibility table was retrieved directly from the `ibkrcampus.com` mirror; other
IB statements are quoted via search extraction of those same pages. Both load-bearing claims — the
individual/OAuth eligibility split and the funded/IBKR-Pro prerequisite — were corroborated from more
than one source. Anything acted on should be re-confirmed in a browser, and the disputed point settled
with IB support.
