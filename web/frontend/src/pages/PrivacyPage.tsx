import { Link } from "react-router-dom";
import { useAuth } from "../App";
import { Logo } from "../components";
import { retentionText, useSite } from "../site";

/** The privacy notice (LGPD art. 9): what is collected, why, who else receives it, for how long, and rights. */
export default function PrivacyPage() {
  const { user } = useAuth();
  const site = useSite();
  const contact = site?.contact;

  return (
    <div className="auth-shell">
      <header className="auth-header"><Logo to="/" large /></header>
      <main className="page medium doc">
        <div className="page-head">
          <h1>Privacy notice</h1>
          <p>How Pidi handles your data, in line with Brazil's data protection law (LGPD, Lei 13.709/2018).</p>
          {site && <p className="subtle small">Version of {site.privacy_version}</p>}
        </div>

        <section>
          <h2>Who is responsible</h2>
          <p>
            {site?.operator
              ? <>Pidi is run by {site.operator}, the controller of the personal data described here.</>
              : <>The operator of this Pidi site is the controller of the personal data described here.</>}
            {contact ? <> For any question or request about your data, write to <a href={`mailto:${contact}`}>{contact}</a>.</> : null}
          </p>
        </section>

        <section>
          <h2>What we keep</h2>
          <ul>
            <li>
              <strong>Your account:</strong> the name, email address and account ID of the Google account you log in
              with. Pidi never sees your Google password.
            </li>
            <li>
              <strong>Your projects:</strong> the recordings you upload, which may show screens, documents, names,
              faces and voices; the screenshots and narration text taken from them; and the documents generated.
            </li>
            <li>
              {site?.billing === "credits" ? (<>
                <strong>Your templates</strong> and your <strong>credits</strong>: how many you were given, bought and spent,
                on which projects, and the credits you bought (amount, price and date; never card details).
              </>) : (<>
                <strong>Your templates</strong> and your <strong>AI provider keys</strong>, stored encrypted and used only to
                process your own projects; only their first and last characters are ever shown.
              </>)}
            </li>
            <li>
              <strong>Security records:</strong> the address your requests come from, to slow down repeated failed
              log-ins, and server logs of errors.
            </li>
          </ul>
        </section>

        <section>
          <h2>Why</h2>
          <p>
            To provide the service you ask for: turning your recordings into process documents (LGPD art. 7, V), and to
            keep the service and accounts secure (art. 7, IX). Your data is not sold, not used for advertising and not
            used to train AI models by Pidi.
          </p>
        </section>

        <section>
          <h2>Who else receives it</h2>
          <ul>
            {site?.billing === "credits" ? (
              <li>
                <strong>OpenAI</strong> (United States) receives the screenshots, audio and text of each project to describe
                the screens, transcribe the narration and write the document. This is an international transfer (art. 33).
                Under OpenAI's API terms, this data isn't used to train their models.
              </li>
            ) : (
              <li>
                <strong>The AI provider you connect</strong> receives the screenshots and text of each project to describe
                the screens and write the document: OpenAI, Anthropic or Google (all in the United States, an international
                transfer under art. 33), under that provider's terms for its API, which say whether it may use the data
                to train its models. With OpenAI it also receives the audio, to transcribe the narration; with the others
                the narration is transcribed on this server. With this server's Ollama, nothing leaves the server.
              </li>
            )}
            <li><strong>Amazon Web Services</strong> hosts the server where the data is stored.</li>
            {site?.billing === "credits" && (
            <li>
              <strong>Asaas</strong> (Brazil), when you buy credits: you pay on its page, which collects the payment
              details it needs (such as your name, CPF and card or Pix data) under its own privacy policy.
            </li>
            )}
            <li><strong>Google</strong>, which you log in with, knows when you log in to Pidi.</li>
          </ul>
        </section>

        <section>
          <h2>Your uploads</h2>
          <p>
            Before uploading a recording you confirm you're allowed to: the people it shows were told about it, and the
            company it belongs to agrees to it being processed as described here.
          </p>
        </section>

        <section>
          <h2>How long</h2>
          <ul>
            <li>
              Recordings (the video, its audio and the screenshots the document doesn't use) are deleted{" "}
              {site ? retentionText(site.retention_days) : "after a set period"}.
            </li>
            <li>Documents and their screenshots stay until you delete the project or your account.</li>
            <li>Your account stays until you delete it.</li>
          </ul>
        </section>

        <section>
          <h2>Your rights</h2>
          <p>Under LGPD art. 18 you can:</p>
          <ul>
            <li>see the data kept about you: download it from your account page;</li>
            <li>correct it: change your name on your account page (the email follows your Google account);</li>
            <li>delete it: delete projects one by one, or your whole account with everything in it;</li>
            <li>
              ask about anything else{contact ? <> at <a href={`mailto:${contact}`}>{contact}</a></> : null}, and complain
              to the national authority (ANPD).
            </li>
          </ul>
        </section>

        <section>
          <h2>Cookies</h2>
          <p>Pidi uses one cookie to keep you logged in, and another for a few minutes while you log in with Google. There are no analytics or advertising cookies.</p>
        </section>

        <p><Link to={user ? "/account" : "/"} className="back">← Back</Link></p>
      </main>
    </div>
  );
}
