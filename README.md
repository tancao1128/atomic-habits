# Atomic Habit & Streak Tracker — Identity OS

A production-ready, cross-platform **Atomic Habit & Streak Tracker** Progressive Web Application (PWA) built for **iOS Safari (iPhone)** and **macOS Desktop**.

## 1. Core Philosophy & Features
- **Identity-Based Habits (James Clear's *Atomic Habits*)**: Every habit tracks:
  - **Name** (Actionable behavior)
  - **Identity Goal** (*"Who I am becoming"*)
  - **Category** (`Research`, `Language`, `Vitality`, `Synthesis`)
  - **Daily Target**
- **"⚡ Never Miss Twice" Recovery Engine**: Automatically detects if a habit was missed yesterday and highlights it at the top of today's queue with an amber recovery badge and priority banner.
- **Real-Time Calendar & 7-Day Week Strip**: Interactive week navigation (`Prev`, `Today`, `Next`) with per-habit completion status dots and daily percentage indicators.

## 2. Analytics & Visualizations
- **Daily Completion Progress Bar** with dynamic percentage and status indicator.
- **Weekly Consistency KPI (%)** & **Best Active Streak Counter (🔥 days)**.
- **Weekly Completion Bar Chart (7 Days)** with interactive day selection.
- **7-Day Interactive Habit Matrix**: Toggle past or current days directly from the matrix table.
- **Identity Alignment by Domain**: Visualizes weekly votes cast across `Research`, `Language`, `Vitality`, and `Synthesis`.

## 3. UI/UX & Responsive Architecture
- **Mobile Viewport (iPhone)**: Single-column layout, thumb-friendly bottom navigation bar (`Today Log`, `7d Matrix`, `New Habit`, `Sync / JSON`), smooth tap micro-interactions, and iOS PWA meta tags (`apple-mobile-web-app-capable`).
- **Desktop Viewport (macOS)**: 2-column Split-View dashboard displaying **Daily Logging** on the left (7 columns) and **Weekly Streak Analytics + 7-Day Interactive Matrix** simultaneously on the right (5 columns).
- **Dark / Light Mode**: Instant theme switching with sleek glassmorphism (`backdrop-filter: blur`) and clean `Inter` + `JetBrains Mono` typography.

## 4. Data Persistence & Portability
- **Full `localStorage` Persistence**: Zero-latency local persistence under key `atomic_habit_streak_os_v1`.
- **JSON Export / Import Backup**: Full portable JSON backup and restore.
- **Cloud Sync Hook**: Built-in REST/Webhook sync adapter ready for **Firebase Realtime Database** or **Supabase REST API**.

## Quick Start

### Option A: Open Directly in Browser
Open [`index.html`](./index.html) directly in Safari or Chrome on macOS.

### Option B: Run Local PWA Server (for iOS Safari / Local Network or Notion Embed)
```bash
python3 -m http.server 8765 --directory /Users/tancao/atomic-habit-tracker
```
Then visit `http://localhost:8765` on your MacBook (or `http://<your-mac-lan-ip>:8765` on iPhone Safari -> **Share** -> **Add to Home Screen**).
