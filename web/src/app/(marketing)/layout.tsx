import { Intro } from "@/components/layout/Intro";
import { Header } from "@/components/layout/Header";
import { Footer } from "@/components/layout/Footer";

const introBoot = `(function(){try{if(window.matchMedia("(prefers-reduced-motion: reduce)").matches)return;if(sessionStorage.getItem("rouvr-intro-seen")==="1")return;document.documentElement.classList.add("intro-play");}catch(e){}})();`;

const introBootCss = `html:not(.intro-play) [data-intro-overlay]{display:none!important}html.intro-play,html.intro-play body{overflow:hidden}html.intro-play [data-intro-overlay]{position:fixed;inset:0;z-index:80;display:flex;align-items:center;justify-content:center;background:#040b13}html.light.intro-play [data-intro-overlay]{background:#f3f6fa}`;

export default function MarketingLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <>
      <script dangerouslySetInnerHTML={{ __html: introBoot }} />
      <style dangerouslySetInnerHTML={{ __html: introBootCss }} />
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-full focus:bg-brand focus:px-4 focus:py-2 focus:text-[#040b13]"
      >
        Skip to content
      </a>
      <Intro />
      <Header />
      <main id="main">{children}</main>
      <Footer />
    </>
  );
}
