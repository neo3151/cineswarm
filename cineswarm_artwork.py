#!/usr/bin/env python3
"""CineSwarm Dynamic Artwork & Poster Generator Engine.

Generates high-resolution theme banners, SVG collection art, and poster graphics
for AI playlists, double features, and curated cinema nights.
"""

from __future__ import annotations

import html
from typing import Any


class DynamicArtworkEngine:
    """Generates dynamic theme artwork and collection posters."""

    THEME_PALETTES = {
        "cyberpunk": {"primary": "#00f0ff", "secondary": "#7000ff", "bg": "#030712", "text": "Cyberpunk Neo-Noir"},
        "80s_synth": {"primary": "#ff007f", "secondary": "#00f0ff", "bg": "#0f051d", "text": "80s Feel-Good Synth"},
        "criterion": {"primary": "#d4af37", "secondary": "#ffffff", "bg": "#111111", "text": "Criterion Collection 4K"},
        "family": {"primary": "#00ff88", "secondary": "#00f0ff", "bg": "#022c22", "text": "Family Cinema Night"},
        "default": {"primary": "#00f0ff", "secondary": "#7000ff", "bg": "#030712", "text": "CineSwarm Special Collection"}
    }

    def generate_svg_banner(self, title: str, theme: str = "default", movie_titles: list[str] | None = None) -> str:
        """Generate a scalable SVG banner for a collection or cinema night."""
        theme_key = theme.lower().replace("-", "_").replace(" ", "_")
        palette = self.THEME_PALETTES.get(theme_key, self.THEME_PALETTES["default"])
        
        c_title = html.escape(title)
        c_sub = html.escape(palette["text"])
        movies_str = html.escape(" • ".join(movie_titles[:4])) if movie_titles else "Curated by CineSwarm AI Swarm"

        svg_code = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 400" width="1200" height="400">
  <defs>
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{palette['bg']}" />
      <stop offset="100%" stop-color="#0a0f1d" />
    </linearGradient>
    <linearGradient id="textGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="{palette['primary']}" />
      <stop offset="100%" stop-color="{palette['secondary']}" />
    </linearGradient>
    <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="8" result="blur" />
      <feComposite in="SourceGraphic" in2="blur" operator="over" />
    </filter>
  </defs>

  <!-- Background Layer -->
  <rect width="1200" height="400" fill="url(#bgGrad)" rx="16" />
  
  <!-- Subtle Grid Pattern -->
  <path d="M0 80 H1200 M0 160 H1200 M0 240 H1200 M0 320 H1200" stroke="rgba(255,255,255,0.03)" stroke-width="1"/>
  <path d="M200 0 V400 M400 0 V400 M600 0 V400 M800 0 V400 M1000 0 V400" stroke="rgba(255,255,255,0.03)" stroke-width="1"/>

  <!-- Decorative Orbs -->
  <circle cx="1000" cy="80" r="140" fill="{palette['primary']}" opacity="0.12" filter="url(#glow)"/>
  <circle cx="150" cy="320" r="160" fill="{palette['secondary']}" opacity="0.12" filter="url(#glow)"/>

  <!-- Header Badge -->
  <rect x="60" y="50" width="220" height="32" rx="16" fill="rgba(255,255,255,0.06)" stroke="{palette['primary']}" stroke-width="1"/>
  <text x="170" y="71" fill="{palette['primary']}" font-family="Inter, sans-serif" font-size="12" font-weight="700" letter-spacing="1.5" text-anchor="middle">{c_sub.upper()}</text>

  <!-- Title Text -->
  <text x="60" y="180" fill="url(#textGrad)" font-family="Inter, sans-serif" font-size="52" font-weight="800" letter-spacing="-1">{c_title}</text>
  
  <!-- Movie List Line -->
  <text x="60" y="235" fill="#94a3b8" font-family="Inter, sans-serif" font-size="18" font-weight="400">{movies_str}</text>

  <!-- Footer Branding -->
  <line x1="60" y1="320" x2="1140" y2="320" stroke="rgba(255,255,255,0.1)" stroke-width="1"/>
  <text x="60" y="355" fill="#64748b" font-family="Fira Code, monospace" font-size="13">CINESWARM SOTA v5.0 • AUTONOMIC MEDIA INTELLIGENCE</text>
  <text x="1140" y="355" fill="{palette['primary']}" font-family="Fira Code, monospace" font-size="13" text-anchor="end">100% LOCAL VECTOR SEARCH</text>
</svg>"""
        return svg_code
