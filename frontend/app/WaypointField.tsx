"use client";

// The animated "waypoint field" — the landing page's ambient background,
// extracted so every screen shares one identity: drifting aurora light,
// faint flight-route arcs with a glint travelling each, breathing beacon
// rings, floating waypoints, and a slow sheen sweep. Pure ambience
// (aria-hidden); all motion is transform/opacity only and is skipped
// entirely under prefers-reduced-motion. Styling lives in globals.css
// (.wp-field / .wp-aurora / .wp-routes / .wp-ring / .wp-pin / .wp-sheen).

import { useRef } from "react";

import gsap from "gsap";
import { useGSAP } from "@gsap/react";

gsap.registerPlugin(useGSAP);

export default function WaypointField() {
  const ref = useRef<HTMLDivElement>(null);

  useGSAP(
    () => {
      // Runs ONLY when the user has no reduced-motion preference; matchMedia
      // reverts it cleanly on unmount. Scoped to this element so it never
      // touches anything else on the page.
      gsap.matchMedia().add("(prefers-reduced-motion: no-preference)", () => {
        gsap.to(".wp-ring", {
          scale: 1.35,
          autoAlpha: 0,
          duration: 4.2,
          ease: "sine.out",
          stagger: { each: 1.4, repeat: -1 },
        });
        gsap.utils.toArray<HTMLElement>(".wp-pin").forEach((pin, i) => {
          gsap.to(pin, {
            y: `+=${16 + i * 6}`,
            x: `+=${i % 2 ? -10 : 12}`,
            duration: 3.5 + i * 0.6,
            ease: "sine.inOut",
            yoyo: true,
            repeat: -1,
          });
        });
        gsap.fromTo(
          ".wp-sheen",
          { xPercent: -120 },
          { xPercent: 120, duration: 9, ease: "sine.inOut", repeat: -1, yoyo: true }
        );
        gsap.utils.toArray<HTMLElement>(".wp-aurora").forEach((blob, i) => {
          gsap.to(blob, {
            xPercent: i % 2 ? -18 : 22,
            yPercent: i % 2 ? 16 : -14,
            scale: 1.15,
            duration: 14 + i * 4,
            ease: "sine.inOut",
            yoyo: true,
            repeat: -1,
          });
        });
        gsap.utils.toArray<SVGPathElement>(".glint").forEach((glint, i) => {
          gsap.fromTo(
            glint,
            { strokeDashoffset: 1030 },
            {
              strokeDashoffset: 30,
              duration: 6.5,
              ease: "power1.inOut",
              repeat: -1,
              repeatDelay: 1.6,
              delay: i * 2.4,
            }
          );
        });
      });
    },
    { scope: ref }
  );

  return (
    <div className="wp-field" aria-hidden="true" ref={ref}>
      <span className="wp-aurora a1" />
      <span className="wp-aurora a2" />
      <span className="wp-aurora a3" />

      <svg className="wp-routes" viewBox="0 0 1280 760" preserveAspectRatio="xMidYMid slice">
        <path className="route" d="M -120 640 Q 420 240 1400 180" />
        <path className="route" d="M -120 300 Q 560 740 1400 500" />
        <path className="route" d="M -120 480 Q 700 380 1400 320" />
        <path className="glint" pathLength={1000} d="M -120 640 Q 420 240 1400 180" />
        <path className="glint" pathLength={1000} d="M -120 300 Q 560 740 1400 500" />
        <path className="glint" pathLength={1000} d="M -120 480 Q 700 380 1400 320" />
      </svg>

      <span className="wp-ring" />
      <span className="wp-ring" />
      <span className="wp-ring" />
      <span className="wp-pin p1" />
      <span className="wp-pin p2" />
      <span className="wp-pin p3" />
      <span className="wp-pin p4" />
      <span className="wp-pin p5" />
      <span className="wp-sheen" />
    </div>
  );
}
