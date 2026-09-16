"use client";

import Image from "next/image";
import { FormEvent, useState } from "react";
import { Eye, EyeOff } from "lucide-react";

import { saveSession } from "@/lib/auth";
import { login } from "@/lib/api";

export default function LoginPage() {
  const [email, setEmail] = useState("admin@university.edu");
  const [password, setPassword] = useState("Admin123*");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      const session = await login(email, password);
      saveSession(session);
      window.location.href = "/dashboard";
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo iniciar sesion");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-paper px-5 py-10">
      <section className="flex items-center justify-center px-5 py-10">
        <form onSubmit={onSubmit} className="w-full max-w-md rounded-[8px] border border-line bg-white p-6 shadow-soft">
          <Image
            src="/logo-ug.png"
            alt="Universidad de Guayaquil"
            width={210}
            height={160}
            priority
            className="mx-auto mb-6 h-auto w-44"
          />
          <h2 className="text-3xl font-semibold">Producción cientifica</h2>

          <label className="mt-8 block text-sm font-medium">Correo institucional</label>
          <input
            className="focus-ring mt-2 w-full rounded-[8px] border border-line px-3 py-3"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            type="email"
          />
          <label className="mt-4 block text-sm font-medium">Contrasena</label>
          <div className="mt-2 flex rounded-[8px] border border-line">
            <input
              className="focus-ring min-w-0 flex-1 rounded-[8px] border-0 px-3 py-3"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type={showPassword ? "text" : "password"}
            />
            <button
              type="button"
              className="focus-ring flex w-12 items-center justify-center rounded-[8px] text-ink/60 hover:text-ink"
              onClick={() => setShowPassword((current) => !current)}
              aria-label={showPassword ? "Ocultar contrasena" : "Ver contrasena"}
            >
              {showPassword ? <EyeOff size={19} /> : <Eye size={19} />}
            </button>
          </div>
          {error ? <p className="mt-4 rounded-[8px] bg-coral/10 p-3 text-sm text-coral">{error}</p> : null}
          <button
            disabled={loading}
            className="focus-ring mt-6 w-full rounded-[8px] bg-ink px-4 py-3 font-semibold text-white disabled:opacity-60"
          >
            {loading ? "Validando..." : "Entrar"}
          </button>
          <p className="mt-4 text-xs text-ink/50">Demo: admin@university.edu / Admin123*</p>
        </form>
      </section>
    </main>
  );
}
