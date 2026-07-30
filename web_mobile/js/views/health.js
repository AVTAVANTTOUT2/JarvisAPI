/* Santé — emplacement réservé.
 *
 * Le sixième onglet existe dès la première version, désaturé tant qu'il est
 * vide. Réserver la place maintenant évite de redessiner la navigation plus
 * tard : passer de cinq à six onglets déplacerait toutes les cibles tactiles
 * et défairait la mémoire musculaire acquise entre-temps.
 *
 * Le module backend tourne déjà (app/fitness/) et expose
 * /api/fitness/{workouts,meals,water/today,wellbeing,summary/today}, mais ses
 * écrans relèvent d'une spécification à part. Les deviner ici produirait une
 * interface à jeter.
 */

import { empty } from '../ui.js';

export default {
  async mount(ctx) {
    ctx.setHeader('Santé');
    ctx.setDock(null);
    ctx.setBody(empty('Section en préparation.', 'Rien à afficher pour le moment.'));
    return () => {};
  },
};
