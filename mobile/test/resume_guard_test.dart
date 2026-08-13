import 'package:flutter_test/flutter_test.dart';
import 'package:ytui_mobile/state/resume_guard.dart';

void main() {
  group('ResumeGuard', () {
    test('un chargement sans reprise laisse tout passer', () {
      final guard = ResumeGuard()..arm(0);
      expect(guard.settled, isTrue);
      expect(guard.target, 0);
      expect(guard.allowsSave(0), isTrue);
      expect(guard.allowsSave(3), isTrue);
    });

    test('une position négative se comporte comme une absence de reprise', () {
      final guard = ResumeGuard()..arm(-12);
      expect(guard.settled, isTrue);
      expect(guard.target, 0);
      expect(guard.allowsSave(1), isTrue);
    });

    test('bloque la sauvegarde tant que le seek n\'a pas atterri', () {
      final guard = ResumeGuard()..arm(600);
      expect(guard.settled, isFalse);
      // Positions du début de flux : ce sont elles qui effaçaient le signet.
      expect(guard.allowsSave(0), isFalse);
      expect(guard.allowsSave(3), isFalse);
      expect(guard.allowsSave(594), isFalse);
    });

    test('tolère l\'imprécision du seek (image-clé la plus proche)', () {
      final guard = ResumeGuard()..arm(600);
      expect(guard.allowsSave(595), isTrue); // 600 - 5
      expect(guard.allowsSave(598), isTrue);
      expect(guard.allowsSave(700), isTrue);
    });

    test('confirm() libère la protection une seule fois', () {
      final guard = ResumeGuard()..arm(600);
      expect(guard.confirm(120), isFalse);
      expect(guard.settled, isFalse);
      expect(guard.confirm(601), isTrue);
      expect(guard.settled, isTrue);
      expect(guard.confirm(602), isFalse); // déjà réglé
      expect(guard.allowsSave(0), isTrue); // l'utilisateur peut revenir au début
    });

    test('release() rend la main à l\'utilisateur', () {
      final guard = ResumeGuard()..arm(600);
      guard.release();
      expect(guard.settled, isTrue);
      expect(guard.target, 0);
      expect(guard.allowsSave(12), isTrue);
    });

    test('un nouveau chargement réarme la protection', () {
      final guard = ResumeGuard()..arm(600);
      guard.confirm(600);
      guard.arm(300);
      expect(guard.settled, isFalse);
      expect(guard.target, 300);
      expect(guard.attempts, 0);
      expect(guard.allowsSave(2), isFalse);
    });

    test('compte les tentatives de seek', () {
      final guard = ResumeGuard()..arm(600);
      guard
        ..noteAttempt()
        ..noteAttempt();
      expect(guard.attempts, 2);
    });

    test('cible inatteignable = signet périmé', () {
      final guard = ResumeGuard()..arm(600);
      expect(guard.unreachable(1200), isFalse);
      expect(guard.unreachable(603), isFalse);
      expect(guard.unreachable(601), isTrue);
      expect(guard.unreachable(400), isTrue);
      // Durée inconnue : on ne conclut rien, on tentera le seek.
      expect(guard.unreachable(0), isFalse);
    });
  });

  group('shouldSeekToResume', () {
    test('aucune cible = aucun seek', () {
      expect(shouldSeekToResume(duration: 600, target: 0), isFalse);
      expect(shouldSeekToResume(duration: 600, target: -5), isFalse);
    });

    test('durée inconnue : on tente quand même', () {
      // C\'est le cas qui faisait repartir la vidéo de zéro : mpv n\'a pas
      // encore publié la durée mais sait déjà chercher.
      expect(shouldSeekToResume(duration: 0, target: 600), isTrue);
    });

    test('cible dans le média : on cherche', () {
      expect(shouldSeekToResume(duration: 600, target: 1), isTrue);
      expect(shouldSeekToResume(duration: 600, target: 300), isTrue);
      expect(shouldSeekToResume(duration: 600, target: 597), isTrue);
    });

    test('cible au-delà (signet périmé) : on repart du début', () {
      expect(shouldSeekToResume(duration: 600, target: 598), isFalse);
      expect(shouldSeekToResume(duration: 600, target: 600), isFalse);
      expect(shouldSeekToResume(duration: 600, target: 900), isFalse);
    });
  });

  group('scénario complet : reprise d\'une vidéo à moitié vue', () {
    test('le point de reprise survit à un seek lent', () {
      final guard = ResumeGuard()..arm(600);
      // Pulsations pendant que le média s\'ouvre : rien ne doit partir.
      for (final position in [0.0, 0.4, 1.2, 2.0]) {
        expect(guard.allowsSave(position), isFalse,
            reason: 'position $position ne doit pas écraser le signet');
      }
      // Le seek atterrit.
      guard.confirm(600);
      // La lecture continue : les pulsations reprennent normalement.
      expect(guard.allowsSave(610), isTrue);
      expect(guard.allowsSave(1200), isTrue);
    });

    test('le seek échoue, puis l\'utilisateur se déplace lui-même', () {
      final guard = ResumeGuard()..arm(600);
      expect(guard.allowsSave(4), isFalse);
      guard.release(); // flèche / barre de progression
      expect(guard.allowsSave(4), isTrue);
    });

    test('signet périmé : libéré, la lecture depuis zéro est enregistrable', () {
      final guard = ResumeGuard()..arm(600);
      expect(shouldSeekToResume(duration: 300, target: guard.target), isFalse);
      guard.release();
      expect(guard.allowsSave(12), isTrue);
    });
  });

  group('shouldReloadForQueueChange — quand rouvrir le média', () {
    test('même vidéo, même place : on ne recharge pas', () {
      expect(
          shouldReloadForQueueChange(
              currentVideoId: 'a', loadedVideoId: 'a', previousIndex: 2, nextIndex: 2),
          isFalse);
    });

    test('vidéo suivante : on recharge', () {
      expect(
          shouldReloadForQueueChange(
              currentVideoId: 'b', loadedVideoId: 'a', previousIndex: 0, nextIndex: 1),
          isTrue);
    });

    test('playlist qui répète un titre : on recharge quand même', () {
      // Le doublon a le même identifiant : sans le test d'index, la lecture
      // restait bloquée sur la fin du premier exemplaire.
      expect(
          shouldReloadForQueueChange(
              currentVideoId: 'a', loadedVideoId: 'a', previousIndex: 0, nextIndex: 1),
          isTrue);
    });

    test('ajout en fin de file pendant la lecture : aucun rechargement', () {
      expect(
          shouldReloadForQueueChange(
              currentVideoId: 'a', loadedVideoId: 'a', previousIndex: 0, nextIndex: 0),
          isFalse);
    });

    test('file vidée : rien à charger', () {
      expect(
          shouldReloadForQueueChange(
              currentVideoId: null, loadedVideoId: 'a', previousIndex: 0, nextIndex: 0),
          isFalse);
    });

    test('premier chargement (aucun média ouvert)', () {
      expect(
          shouldReloadForQueueChange(
              currentVideoId: 'a', loadedVideoId: null, previousIndex: null, nextIndex: 0),
          isTrue);
    });
  });

  group('shouldHandleCompletion — une seule avance par vidéo', () {
    test('première fin de lecture : on enchaîne', () {
      expect(shouldHandleCompletion(loadedVideoId: 'a', alreadyHandledFor: null),
          isTrue);
    });

    test('`completed` réémis pour la même vidéo : on ignore', () {
      // C'est ce doublon qui faisait sauter une vidéo de la playlist.
      expect(shouldHandleCompletion(loadedVideoId: 'a', alreadyHandledFor: 'a'),
          isFalse);
    });

    test('vidéo suivante terminée : on enchaîne de nouveau', () {
      expect(shouldHandleCompletion(loadedVideoId: 'b', alreadyHandledFor: 'a'),
          isTrue);
    });

    test('aucun média ouvert : rien à enchaîner', () {
      expect(
          shouldHandleCompletion(loadedVideoId: null, alreadyHandledFor: null),
          isFalse);
    });
  });
}
