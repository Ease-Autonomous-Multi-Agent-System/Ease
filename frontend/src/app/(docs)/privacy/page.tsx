import type { Metadata } from "next";
import Link from "next/link";
import { REPO_URL } from "@/lib/config";

export const metadata: Metadata = {
  title: "Privacy and terms · Ease",
  description: "What Ease stores, who it is shared with, how to delete it, and the rules for using Ease.",
};

const STORED: [string, string, string][] = [
  ["Account", "Email, name, password (as a bcrypt hash, never the password itself)", "Until you delete your account"],
  ["Profile and resume", "The resume you upload, the details read from it, and a numeric summary (embedding) used for matching", "Until you delete the document or your account"],
  ["Your keys", "AI model and app keys you add, encrypted with AES-256-GCM. Never shown again, never sent to an AI model", "Until you disconnect them or delete your account"],
  ["Website logins", "Only if a sign-in page blocks a run and you choose to give them. Encrypted; typed by the browser, never seen by the AI", "Deleted when the run ends (unless you tick “remember”, on servers that allow it)"],
  ["Runs", "Your instructions, each step, the results, and screenshots of the pages the agent opened", "Until you delete your account"],
  ["Security log", "Sign-ins and important actions with the IP address, to detect abuse", "Kept without your user id after you delete your account"],
];

export default function PrivacyPage() {
  return (
    <article className="stack" style={{ gap: 24 }}>
      <header>
        <h1>Privacy and terms</h1>
        <p className="muted">Ease is a student project from ABES Engineering College, run free of charge. This page says plainly what it does with your data.</p>
      </header>

      <section className="stack" style={{ gap: 10 }}>
        <h2>What Ease stores</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th>What</th><th>Details</th><th>How long</th></tr></thead>
            <tbody>{STORED.map(([a, b, c]) => <tr key={a}><td>{a}</td><td>{b}</td><td>{c}</td></tr>)}</tbody>
          </table>
        </div>
      </section>

      <section className="stack" style={{ gap: 10 }}>
        <h2>Who sees it</h2>
        <ul>
          <li><strong>AI providers you choose.</strong> To plan and act, Ease sends your instruction and the text (and sometimes a screenshot) of the pages it opens to the AI service whose key you added: Groq, Google Gemini or OpenRouter. Their privacy policies apply to what they receive.</li>
          <li><strong>Apps you connect</strong> (Notion, Google Sheets, Telegram, Slack) receive only the results you asked to send there.</li>
          <li><strong>Nobody else.</strong> Your data is not sold, not used for advertising and not shared with anyone outside the project team, who can reach the server only to keep it running.</li>
        </ul>
      </section>

      <section className="stack" style={{ gap: 10 }}>
        <h2>Your rights</h2>
        <ul>
          <li><strong>See and correct</strong> your profile on the <Link href="/settings">Profile &amp; apps</Link> page.</li>
          <li><strong>Remove</strong> your resume, keys or saved logins there at any time.</li>
          <li><strong>Delete everything:</strong> “Delete account” at the bottom of that page permanently removes your account, resume, keys, logins and every run with its screenshots, straight away.</li>
          <li>Questions or complaints: open an issue on <a href={REPO_URL} target="_blank" rel="noopener noreferrer">GitHub</a>.</li>
        </ul>
        <p className="small muted">These rights follow India&apos;s Digital Personal Data Protection Act, 2023.</p>
      </section>

      <section className="stack" style={{ gap: 10 }}>
        <h2>Terms of use</h2>
        <ul>
          <li><strong>You are in charge.</strong> Ease acts on your instructions. Anything irreversible (submitting a form, applying, sending) waits for your approval: read what it is about to do before you approve it.</li>
          <li><strong>Use it lawfully.</strong> No spam, no fake accounts or applications, no harassment, and no collecting other people&apos;s personal data. Respect the rules of the websites you use it on.</li>
          <li><strong>Some sites are off limits.</strong> Ease never opens banking, payment, crypto or government-identity websites, and it follows each site&apos;s robots.txt.</li>
          <li><strong>Your keys, your limits.</strong> Runs use the AI keys you add; any limits or charges from those providers are between you and them.</li>
          <li><strong>No guarantees.</strong> Ease is a research prototype. Results can be wrong or incomplete, the service can be slow, change or stop, and it is provided as is, without warranty.</li>
          <li><strong>Accounts that break these rules</strong> or overload the service may be suspended.</li>
        </ul>
      </section>
    </article>
  );
}
