# Script d'installation NetSentinel
# A lancer une seule fois apres git clone
# Usage : .\setup.ps1

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Write-Step($n, $msg) {
    Write-Host ""
    Write-Host "  [$n] $msg" -ForegroundColor Yellow
}

function Write-OK($msg) {
    Write-Host "  ok -- $msg" -ForegroundColor Green
}

function Write-Fail($msg) {
    Write-Host "  erreur -- $msg" -ForegroundColor Red
    Read-Host "Entree pour quitter"
    exit 1
}

Clear-Host
Write-Host ""
Write-Host "  +--------------------------------------+" -ForegroundColor Cyan
Write-Host "  |      NetSentinel -- Setup            |" -ForegroundColor Cyan
Write-Host "  +--------------------------------------+" -ForegroundColor Cyan
Write-Host ""

# ── 1. Git pull ───────────────────────────────────────────────────────────────
Write-Step "1/6" "Mise a jour du depot (git pull)"
git pull origin main
if (-not $?) { Write-Fail "git pull a echoue -- verifiez votre connexion" }
Write-OK "Depot a jour"

# ── 2. Python ─────────────────────────────────────────────────────────────────
Write-Step "2/6" "Verification Python (>= 3.10)"
try {
    $ver = python --version 2>&1
    Write-OK $ver
} catch {
    Write-Fail "Python introuvable -- installez Python 3.10+"
}

# ── 2. Environnement virtuel ───────────────────────────────────────────────────
Write-Step "2/6" "Environnement virtuel"
if (Test-Path ".venv") {
    Write-OK ".venv deja present, on passe"
} else {
    Write-Host "  Creation du venv..." -ForegroundColor DarkGray
    python -m venv .venv
    if (-not $?) { Write-Fail "Impossible de creer le venv" }
    Write-OK ".venv cree"
}

# Activation
.\.venv\Scripts\Activate.ps1

# ── 3. Dependances ────────────────────────────────────────────────────────────
Write-Step "3/6" "Installation des dependances (pip install -e .)"
Write-Host "  Cela peut prendre quelques minutes..." -ForegroundColor DarkGray
pip install -e . --quiet
if (-not $?) { Write-Fail "pip install a echoue" }
Write-OK "Dependances installees"

# ── 4. DVC pull ───────────────────────────────────────────────────────────────
Write-Step "4/6" "Telechargement des donnees (DVC -- Google Drive)"
Write-Host "  Une fenetre de connexion Google va s'ouvrir..." -ForegroundColor DarkGray
dvc pull
if (-not $?) { Write-Fail "dvc pull a echoue -- verifiez votre acces Google Drive" }
Write-OK "Donnees telechargees"

# ── 5. Pipeline Kedro ─────────────────────────────────────────────────────────
Write-Step "6/6" "Execution du pipeline Kedro (training + reporting)"
Write-Host "  Cela peut prendre plusieurs minutes selon votre machine..." -ForegroundColor DarkGray
kedro run
if (-not $?) { Write-Fail "kedro run a echoue -- consultez les logs ci-dessus" }
Write-OK "Pipeline termine"

# ── Fin ───────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  +--------------------------------------+" -ForegroundColor Green
Write-Host "  |  Installation terminee !             |" -ForegroundColor Green
Write-Host "  +--------------------------------------+" -ForegroundColor Green
Write-Host ""
Write-Host "  Pour voir les metriques du modele :"
Write-Host "    mlflow ui --port 5000  ->  http://127.0.0.1:5000"
Write-Host ""
Write-Host "  Pour lancer la demo streaming :"
Write-Host "    .\start_demo.ps1"
Write-Host ""
