import "@fontsource/manrope/400.css";
import "@fontsource/manrope/500.css";
import "@fontsource/manrope/600.css";
import "@fontsource/manrope/700.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "./globals.css";
import { AppNavigation } from "../components/design";
export const metadata = {
  title: "Tandem · A plan for everyone",
  description: "Your people. Your own companion. A plan everyone gets a say in.",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body><AppNavigation />{children}</body>
    </html>
  );
}
