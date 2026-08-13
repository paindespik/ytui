/// Protection du point de reprise, partagée par le lecteur mobile.
///
/// Le problème qu'elle résout : entre `open()` et l'atterrissage du seek de
/// reprise, le lecteur annonce une position proche de zéro. La pulsation qui
/// sauvegarde la position toutes les 10 s (ou le flush final quand on quitte
/// l'écran) écrivait alors ce zéro par-dessus le point de reprise — la vidéo
/// « oubliait » où on en était, de façon intermittente, selon que le seek avait
/// eu le temps d'atterrir ou non.
///
/// La règle : tant que la reprise demandée n'est pas confirmée, aucune position
/// sensiblement inférieure à la cible n'est sauvegardée. La protection tombe
/// quand le seek atterrit, quand l'utilisateur se déplace lui-même, ou quand la
/// cible se révèle inatteignable (durée réelle plus courte que le marque-page).
library;

/// Marge d'imprécision d'un seek (mpv atterrit sur l'image-clé la plus proche).
const double kResumeToleranceSeconds = 5;

/// Un marque-page à moins de cette distance de la fin est considéré comme
/// « vidéo terminée » : inutile de chercher à s'y rendre.
const double kResumeEndMarginSeconds = 2;

class ResumeGuard {
  double _target = 0;
  bool _settled = true;
  int _attempts = 0;

  /// Position de reprise visée pour le média en cours (0 = aucune).
  double get target => _target;

  /// La reprise est-elle réglée (atterrie, abandonnée, ou jamais demandée) ?
  bool get settled => _settled;

  int get attempts => _attempts;

  /// Arme la protection pour un nouveau chargement démarrant à [start].
  void arm(double start) {
    _target = start > 0 ? start : 0;
    _settled = _target == 0;
    _attempts = 0;
  }

  /// À appeler avant chaque tentative de seek (compteur d'essais).
  void noteAttempt() => _attempts += 1;

  /// Confirme la reprise si [position] est arrivée (ou a dépassé) la cible.
  /// Renvoie `true` quand la protection vient de tomber.
  bool confirm(double position) {
    if (_settled) return false;
    if (position >= _target - kResumeToleranceSeconds) {
      _settled = true;
      return true;
    }
    return false;
  }

  /// Déplacement volontaire de l'utilisateur : sa position fait foi.
  void release() {
    _settled = true;
    _target = 0;
  }

  /// La cible est-elle atteignable dans un média de durée [duration] ?
  /// `false` pour une durée inconnue (0) : on tente quand même le seek.
  bool unreachable(double duration) =>
      duration > 0 && _target >= duration - kResumeEndMarginSeconds;

  /// Peut-on écrire [position] comme nouvelle position de reprise ?
  bool allowsSave(double position) =>
      _settled || position >= _target - kResumeToleranceSeconds;
}

/// Faut-il rouvrir un média parce que la file a bougé ?
///
/// Comparer les seules identités de vidéo ne suffit pas : une playlist peut
/// répéter un titre, et la lecture restait alors bloquée sur la fin du doublon
/// (même identifiant → aucun rechargement → file arrêtée). Un déplacement dans
/// la file rouvre donc le média même à identifiant identique.
bool shouldReloadForQueueChange({
  required String? currentVideoId,
  required String? loadedVideoId,
  required int? previousIndex,
  required int nextIndex,
}) {
  if (currentVideoId == null) return false;
  if (currentVideoId != loadedVideoId) return true;
  return previousIndex != null && previousIndex != nextIndex;
}

/// Faut-il traiter cette fin de lecture ?
///
/// media_kit réémet `completed` (et le réarme à chaque `open()`) : sans ce
/// filtre, la file avançait deux fois et sautait une vidéo.
bool shouldHandleCompletion({
  required String? loadedVideoId,
  required String? alreadyHandledFor,
}) =>
    loadedVideoId != null && loadedVideoId != alreadyHandledFor;

/// Faut-il chercher à rejoindre [target] dans un média de durée [duration] ?
///
/// Une durée nulle veut dire « pas encore connue » : on tente quand même, mpv
/// sait souvent chercher avant d'avoir publié la durée. Une cible au-delà (ou à
/// deux secondes) de la fin réelle est un marque-page périmé : on repart du
/// début plutôt que de rester bloqué à la fin.
bool shouldSeekToResume({required double duration, required double target}) {
  if (target <= 0) return false;
  if (duration <= 0) return true;
  return target < duration - kResumeEndMarginSeconds;
}
