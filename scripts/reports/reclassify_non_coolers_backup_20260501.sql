-- Откат: вернуть найденные id в is_hidden = FALSE.
UPDATE coolers SET is_hidden = FALSE WHERE id IN (541, 544, 546, 551, 553, 558, 559, 568, 577, 581, 1073, 1081, 1083, 1094, 1194, 1665, 1670, 1671);
