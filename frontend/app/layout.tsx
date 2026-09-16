import type { Metadata } from "next";
import "./globals.css";
import { DataCacheProvider } from "@/lib/data-cache";
import { FilterProvider } from "@/lib/filters";

export const metadata: Metadata = {
  title: "Control científico — FCA",
  description: "Control de producción científica de la Facultad de Ciencias Administrativas"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body>
        <FilterProvider>
          <DataCacheProvider>{children}</DataCacheProvider>
        </FilterProvider>
      </body>
    </html>
  );
}
