"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";

import { AnswerCard, ModelBadge, ProblemCard } from "@/components/answer-card";
import { ArrowIcon, Button, Callout, Card, ContrastIcon } from "@/components/ui";
import { type Answer, askQuestion, type Problem } from "@/lib/api";
import type { Context } from "@/lib/backend";
import { modelName, mustConfirmExternal } from "@/lib/format";

type Entry = {
  id: number;
  question: string;
  startedAt: number;
  answer?: Answer;
  problem?: Problem;
};

const MAX_LENGTH = 300;
const HISTORY = 12;

export function Workspace({ context }: { context: Context }) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [question, setQuestion] = useState("");
  // La confirmación del aviso de privacidad dura hasta recargar la página (TD-021).
  const [confirmed, setConfirmed] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const counter = useRef(0);
  const pending = entries.some((entry) => !entry.answer && !entry.problem);
  const blocked = mustConfirmExternal(context.llm, confirmed);

  async function ask(text: string) {
    const trimmed = text.trim();
    if (trimmed.length < 3 || pending || blocked) return;
    counter.current += 1;
    const id = counter.current;
    setQuestion("");
    setEntries((current) =>
      [{ id, question: trimmed, startedAt: Date.now() }, ...current].slice(0, HISTORY),
    );
    const outcome = await askQuestion(trimmed);
    setEntries((current) =>
      current.map((entry) =>
        entry.id !== id
          ? entry
          : outcome.ok
            ? { ...entry, answer: outcome.answer }
            : { ...entry, problem: outcome.problem },
      ),
    );
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(question);
  }

  return (
    <div className="mx-auto max-w-7xl px-4 pb-16 sm:px-6">
      <Header context={context} />
      <div className="grid gap-8 lg:grid-cols-[17rem_minmax(0,1fr)]">
        <main className="min-w-0 lg:order-2">
          {context.llm?.external && (
            <div className="mb-4">
              <Callout title="Las preguntas salen del equipo" tone="warning">
                Responde {modelName(context.llm.provider, context.llm.model)}, un servicio externo.
                Sus condiciones de prueba prohíben enviar datos personales o confidenciales y le
                permiten usar lo enviado para mejorar sus modelos. No escribas nombres, correos,
                teléfonos ni identificadores: si la pregunta parece llevarlos, no se envía.
                <label className="mt-3 flex items-center gap-2 font-medium">
                  <input
                    type="checkbox"
                    checked={confirmed}
                    onChange={(event) => setConfirmed(event.target.checked)}
                  />
                  Entendido: mis preguntas no llevarán datos personales
                </label>
              </Callout>
            </div>
          )}
          <form onSubmit={submit}>
            <Card className="p-4 focus-within:border-accent sm:p-5">
              <label htmlFor="question" className="text-sm font-medium">
                Pregunta en lenguaje natural
              </label>
              <textarea
                id="question"
                ref={input}
                value={question}
                maxLength={MAX_LENGTH}
                rows={2}
                placeholder="Por ejemplo: ¿cuáles son las ventas de los últimos 3 meses?"
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void ask(question);
                  }
                }}
                className="mt-2 w-full resize-none bg-transparent text-lg outline-none placeholder:text-muted"
              />
              <div className="mt-2 flex items-center justify-between gap-4">
                <p className="text-xs text-muted">
                  {blocked
                    ? "Confirma el aviso para enviar la pregunta."
                    : "Solo lectura, sobre la capa Gold."}
                </p>
                <Button type="submit" disabled={pending || blocked || question.trim().length < 3}>
                  Preguntar
                  <ArrowIcon />
                </Button>
              </div>
            </Card>
          </form>

          {context.llm === null && (
            <div className="mt-6">
              <Callout title="La API no responde" tone="critical">
                Comprueba que el backend está en marcha y recarga la página.
              </Callout>
            </div>
          )}

          <div className="mt-6 space-y-6" aria-live="polite">
            {entries.map((entry) =>
              entry.answer ? (
                <AnswerCard key={entry.id} answer={entry.answer} />
              ) : entry.problem ? (
                <ProblemCard key={entry.id} question={entry.question} problem={entry.problem} />
              ) : (
                <Waiting key={entry.id} entry={entry} context={context} />
              ),
            )}
          </div>
          {entries.length === 0 && <Intro context={context} />}
        </main>
        <Examples
          context={context}
          disabled={pending || blocked}
          onPick={(text) => void ask(text)}
        />
      </div>
    </div>
  );
}

function Header({ context }: { context: Context }) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 py-5">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Odoo Data Lakehouse</h1>
        <p className="text-sm text-muted">Los datos del ERP, preguntando en español</p>
      </div>
      <div className="flex items-center gap-2">
        {context.llm !== null && <ModelBadge llm={context.llm} />}
        <ThemeToggle />
      </div>
    </header>
  );
}

function ThemeToggle() {
  function toggle() {
    const root = document.documentElement;
    const dark =
      root.dataset.theme === "dark" ||
      (root.dataset.theme === undefined &&
        window.matchMedia("(prefers-color-scheme: dark)").matches);
    root.dataset.theme = dark ? "light" : "dark";
  }
  return (
    <Button variant="ghost" onClick={toggle} aria-label="Cambiar entre tema claro y oscuro">
      <ContrastIcon />
    </Button>
  );
}

function Examples({
  context,
  disabled,
  onPick,
}: {
  context: Context;
  disabled: boolean;
  onPick: (question: string) => void;
}) {
  if (context.examples.length === 0) return null;
  return (
    <nav aria-label="Preguntas de ejemplo" className="lg:order-1">
      <h2 className="text-sm font-semibold">Ejemplos por dominio</h2>
      <div className="mt-3 grid gap-5 sm:grid-cols-2 lg:grid-cols-1">
        {context.examples.map((group) => (
          <section key={group.domain} aria-labelledby={`domain-${group.domain}`}>
            <h3 id={`domain-${group.domain}`} className="text-xs font-medium text-muted">
              {group.label}
            </h3>
            <ul className="mt-1">
              {group.questions.map((question) => (
                <li key={question}>
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => onPick(question)}
                    className="w-full rounded-md px-2 py-1.5 text-left text-sm hover:bg-raised disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {question}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </nav>
  );
}

function Waiting({ entry, context }: { entry: Entry; context: Context }) {
  const [now, setNow] = useState(entry.startedAt);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.round((now - entry.startedAt) / 1000));
  const model = context.llm ? modelName(context.llm.provider, context.llm.model) : "el modelo";
  return (
    <Card className="p-5 sm:p-6" aria-label={entry.question}>
      <p className="text-sm text-muted">{entry.question}</p>
      <p className="mt-2 font-medium">
        {model} está escribiendo la consulta
        <span className="text-muted tabular-nums"> · {seconds} s</span>
      </p>
      <div className="mt-4 h-1 overflow-hidden rounded-full bg-raised">
        <div className="sweep h-full w-1/4 rounded-full bg-series" />
      </div>
      {seconds >= 8 && (
        <p className="mt-3 text-sm text-muted">
          La primera pregunta tarda más: el modelo se está cargando en la GPU.
        </p>
      )}
    </Card>
  );
}

const DOMAINS: Record<string, string> = {
  ventas: "Ventas",
  facturacion: "Facturación",
  inventario: "Inventario",
  crm: "CRM",
  maestros: "Clientes y productos",
};

function Intro({ context }: { context: Context }) {
  if (context.tables.length === 0) return null;
  return (
    <section className="mt-10" aria-labelledby="intro">
      <h2 id="intro" className="text-sm font-semibold">
        Qué puedes preguntar
      </h2>
      <div className="mt-3 grid gap-x-8 gap-y-6 sm:grid-cols-2">
        {Object.entries(DOMAINS).map(([domain, label]) => (
          <div key={domain}>
            <h3 className="font-medium">{label}</h3>
            <ul className="mt-1 space-y-1 text-sm text-muted">
              {context.tables
                .filter((table) => table.domain === domain)
                .map((table) => (
                  <li key={table.name}>{table.description.split(". ")[0]?.replace(/\.$/, "")}.</li>
                ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
