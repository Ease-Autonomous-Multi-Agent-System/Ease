import type { Metadata } from "next";
import Link from "next/link";
import { REPO_URL } from "@/lib/config";

export const metadata: Metadata = {
  title: "Getting started with Ease",
  description: "Set up your Ease account: sign up, add your details, connect apps, run your first task and approve actions.",
};

const SERVICES: [string, string, string, string][] = [
  ["Web search (Tavily)", "Search the web when you don't name a website", "tavily.com → sign up → copy the API key (tvly-…). Free: 1,000 searches a month.", "Tavily API key"],
  ["Price comparison (Serper)", "\"Where is this cheapest?\" across online stores", "serper.dev → sign up → API key. Free: 2,500 searches, once.", "Serper API key"],
  ["Notion", "Log results as rows in a database", "notion.so/my-integrations → New integration → copy the token (ntn_…). Open your database → ••• → Connections → add the integration. The database ID is the 32-character code in its URL.", "Integration token + Database ID"],
  ["Telegram", "Get a message when a run finishes", "In Telegram, message @BotFather → /newbot → copy the token. Send \"hi\" to your bot, open api.telegram.org/bot<token>/getUpdates and copy the number after \"chat\":{\"id\":.", "Bot token + Your chat ID"],
  ["Slack", "Post summaries to a channel", "api.slack.com/apps → Create app → Incoming Webhooks → On → Add New Webhook → pick a channel → copy the URL (https://hooks.slack.com/…).", "Incoming webhook URL"],
  ["Google Sheets", "Append rows to a spreadsheet", "Google Cloud console → create a service account → Keys → Add key → JSON. Share your sheet with the service account's email (Editor).", "Service account JSON"],
];

const EXAMPLES = [
  ["Research", "Get the 5 most recent cs.AI papers from arXiv and summarise each in two lines."],
  ["Prices", "Where can I buy a Casio F-91W cheapest in India? Only genuine sellers."],
  ["Browse", "On https://books.toscrape.com/ list the books on the first page under 20 pounds, with prices."],
  ["Look up", "Find the 3 most cited papers about large language model agents using OpenAlex."],
  ["Jobs", "List 5 engineering jobs currently open at Stripe and rank them against my resume."],
  ["Forms", "Fill the application form at <link to a form> using my profile."],
];

const TROUBLE: [string, string][] = [
  ["\"Waiting for you\" and nothing moves", "The run is paused for your approval. Look at the panel on the right of the run page, approve or reject, and it continues."],
  ["\"This step needs your resume\"", "Upload it under Profile & apps, then choose Try again in the approval panel."],
  ["\"The site is showing a bot check / CAPTCHA\"", "Ease never solves CAPTCHAs. Open the site yourself, pass the check, then choose Try again, or skip the step."],
  ["\"… asks automated agents not to open this page\"", "The site's robots.txt blocks agents, so Ease respects it. Name another site, or connect web search so it can use an API instead."],
  ["The run fails with a rate-limit message", "The free AI models allow only so many requests per minute and per day. Wait a minute and use New task to run it again. The counter in the top bar shows your AI calls today."],
  ["A seller is marked \"Not trusted\"", "Hover the badge to see why (for example a price far below every other store, or a look-alike web address). Ease never recommends these sellers."],
];

export default function GuidePage() {
  return (
    <>
      <section>
        <span className="eyebrow">Getting started</span>
        <h1 style={{ fontSize: 36 }}>Set up your Ease account</h1>
        <p className="lead">
          Five minutes from sign-up to your first run. Only step 1 is required; everything else unlocks more kinds of
          tasks.
        </p>
        <nav className="toc" aria-label="On this page">
          <a href="#account">1 · Account</a><a href="#profile">2 · Your details</a><a href="#apps">3 · Connect apps</a>
          <a href="#first">4 · First task</a><a href="#approve">5 · Approvals</a><a href="#results">6 · Results</a>
          <a href="#rules">What Ease won&apos;t do</a><a href="#help">Troubleshooting</a><a href="#self-host">Run it yourself</a>
        </nav>
      </section>

      <section id="account">
        <h2>1 · Create your account</h2>
        <ol className="steps">
          <li><div>Open the app and choose <b>Create an account</b> (<Link href="/register">sign up here</Link>).</div></li>
          <li><div>Enter your name, email and a password of at least 10 characters. Use a password you don&apos;t use anywhere else.</div></li>
          <li><div>If the person running this Ease server gave you an <b>invite code</b>, enter it. Shared deployments require one, so strangers can&apos;t use up the AI quota.</div></li>
          <li><div>You stay signed in on this browser. Your session renews itself securely and ends when you sign out.</div></li>
        </ol>
      </section>

      <section id="profile">
        <h2>2 · Add your details (optional)</h2>
        <p>Open <b>Profile &amp; apps</b> from the top bar.</p>
        <ol className="steps">
          <li><div><b>Upload your resume</b> (PDF or text, up to 5 MB). Ease reads it on the server, fills in your profile, and uses it to rank jobs or courses against your experience. It shows <i>Ready</i> when it is done.</div></li>
          <li><div><b>Check &quot;Your profile&quot;</b>: name, email, phone, college, degree, graduation year, links. This is exactly what the agent types into forms, so fix anything that was read wrongly and choose <b>Save profile</b>.</div></li>
        </ol>
        <p className="small muted">Skip this if you only want research, price comparison or browsing: those tasks don&apos;t need your details.</p>
      </section>

      <section id="apps">
        <h2>3 · Connect apps (optional)</h2>
        <p>
          In <b>Profile &amp; apps → Services</b>, paste a key into a card and choose <b>Connect</b>. Every service
          below has a free tier and needs no credit card. Ease only offers the AI a tool once its key is connected.
        </p>
        <div className="table-wrap card card-flush">
          <table className="data">
            <thead><tr><th>Service</th><th>What it unlocks</th><th>How to get the key</th><th>What you paste</th></tr></thead>
            <tbody>
              {SERVICES.map(([name, what, how, paste]) => (
                <tr key={name}><td><b style={{ fontWeight: 500 }}>{name}</b></td><td>{what}</td><td className="small muted">{how}</td><td className="small">{paste}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="small muted">
          Keys are encrypted (AES-256-GCM) and write-only: after saving, you only ever see the last few characters.
          They are never shown to an AI model and are decrypted only at the moment a step uses them. Choose
          <b> Disconnect</b> to delete one.
        </p>
      </section>

      <section id="first">
        <h2>4 · Run your first task</h2>
        <ol className="steps">
          <li><div>Go to <b>New task</b>, type what you want in plain English, and choose <b>Start</b>. The example buttons under the box fill in ready-made tasks.</div></li>
          <li><div>
            Be specific: name the website if you have one in mind, say how many results you want, and which details
            matter (price, date, rating…). For example:
            <div className="table-wrap card card-flush" style={{ marginTop: 8 }}>
              <table className="data"><tbody>{EXAMPLES.map(([k, v]) => <tr key={k}><td><b style={{ fontWeight: 500 }}>{k}</b></td><td>{v}</td></tr>)}</tbody></table>
            </div>
          </div></li>
          <li><div>
            The run page shows everything live: the <b>Plan</b> (each step, with API, Browser or Matching, and its
            status), the <b>Agent&apos;s view</b> (the page the browser agent is looking at, with the numbers it
            clicks by), and a <b>Details · event log</b> at the bottom. You can choose <b>Stop</b> at any time.
          </div></li>
        </ol>
      </section>

      <section id="approve">
        <h2>5 · Approve or reject actions</h2>
        <p>When a step would do something you can&apos;t undo, like submitting a form or sending a message, the run pauses and shows <b>Waiting for you</b>. A panel opens on the right:</p>
        <ol className="steps">
          <li><div>Look at the <b>screenshot of the filled form</b> and the list of fields the agent filled.</div></li>
          <li><div><b>Edit any field</b> directly in the panel if something is wrong. Changed fields are highlighted.</div></li>
          <li><div>Choose <b>Approve and submit</b> (or <b>Approve with N edits</b>) to go ahead, or <b>Skip this step</b>. Ease replays the form in a fresh browser with your edits, so it is safe to approve even hours later.</div></li>
          <li><div>If the agent gets stuck (a CAPTCHA, a login page, a missing resume), the same panel explains what it needs. Fix it, then choose <b>Try again</b>, or skip the step.</div></li>
        </ol>
      </section>

      <section id="results">
        <h2>6 · Read results and ask follow-ups</h2>
        <ul>
          <li>The <b>Done</b> card shows a short answer and a table of everything found. Titles link to the source.</li>
          <li>In price comparisons, each seller has a badge: <b>Trusted</b> (well-known retailer or official brand store), <b>Unverified</b> (unknown store: check reviews before paying) or <b>Not trusted</b> (red flags; never recommended). Hover a badge to see why.</li>
          <li>Use <b>Ask a follow-up</b> under the result, for example &quot;Which of these has the best rating?&quot;. Ease reuses this run&apos;s results instead of searching again.</li>
          <li><b>History</b> keeps every run, with filters for runs waiting for you, running, completed and failed.</li>
        </ul>
      </section>

      <section id="rules">
        <h2>What Ease won&apos;t do</h2>
        <ul>
          <li>Pay, buy or transfer money. It finds and prepares; you complete the purchase yourself.</li>
          <li>Type passwords, card numbers or OTPs, create accounts on other sites, or solve CAPTCHAs.</li>
          <li>Submit, send or delete anything without your approval.</li>
          <li>Follow instructions written inside web pages. Only you give it instructions.</li>
          <li>Open search engines directly or ignore a site&apos;s robots.txt.</li>
        </ul>
      </section>

      <section id="help">
        <h2>Troubleshooting</h2>
        <div className="table-wrap card card-flush">
          <table className="data">
            <thead><tr><th>You see</th><th>What to do</th></tr></thead>
            <tbody>{TROUBLE.map(([a, b]) => <tr key={a}><td><b style={{ fontWeight: 500 }}>{a}</b></td><td className="muted">{b}</td></tr>)}</tbody>
          </table>
        </div>
      </section>

      <section id="self-host">
        <h2>Run your own Ease server</h2>
        <p>Everything is free and open source. On a machine with Docker Desktop (8 GB of memory) and Git:</p>
        <ol className="steps">
          <li><div>
            Get the code and your settings file:
            <pre className="card mono small" style={{ margin: 0, overflowX: "auto" }}>{`git clone ${REPO_URL}.git
cd Ease
cp .env.example .env`}</pre>
          </div></li>
          <li><div>
            In <code>.env</code>, paste a free <b>GROQ_API_KEY</b> (console.groq.com) and <b>GEMINI_API_KEY</b>
            (aistudio.google.com), and generate the two secrets as the file explains. Set <b>REGISTRATION_INVITE_CODE</b>{" "}
            if other people will use your server.
          </div></li>
          <li><div>
            Start everything:
            <pre className="card mono small" style={{ margin: 0, overflowX: "auto" }}>docker compose --profile web up -d --build</pre>
          </div></li>
          <li><div>Open <b>http://localhost:3000</b> and create your account. The first start takes a few minutes while images build. The full developer guide is in the <a href={REPO_URL} target="_blank" rel="noopener noreferrer">README</a>.</div></li>
        </ol>
      </section>
    </>
  );
}
