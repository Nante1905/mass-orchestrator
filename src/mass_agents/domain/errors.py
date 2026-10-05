"""Les erreurs que ce service distingue.

La distinction qui compte est celle entre ce que l'appelant peut corriger et ce
qu'il ne peut pas. Une session expirée se règle en se reconnectant ; un plafond
atteint se règle en reformulant ; une panne du serveur MCP ne se règle pas du
tout côté navigateur. Les confondre en une seule 500 fait chercher au mauvais
endroit — et fait réessayer en boucle ce qui ne marchera jamais.
"""

from __future__ import annotations


class MassAgentsError(Exception):
    """Racine commune, pour qu'un `except` puisse cadrer sans tout attraper."""


class AdminAuthError(MassAgentsError):
    """Le jeton n'ouvre rien : absent, invalide, expiré, ou compte rétrogradé.

    `status` reprend la distinction de `mass-backend` : 401 quand le jeton ne
    vaut rien, 403 quand il est authentique mais que le compte n'est plus
    administrateur. Un 403 rendu en 401 enverrait l'utilisateur se reconnecter
    pour rien.
    """

    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class ThreadNotFoundError(MassAgentsError):
    """Fil inconnu, ou appartenant à quelqu'un d'autre.

    Les deux cas rendent volontairement la même erreur : distinguer « ce fil
    n'existe pas » de « ce fil ne vous appartient pas » dirait à un
    administrateur curieux quels identifiants sont réels.
    """


class ThreadConflictError(MassAgentsError):
    """La demande est valide, mais pas dans l'état où se trouve le fil.

    Rendue avant l'ouverture du flux, en 409 : une fois les en-têtes SSE
    envoyés, un refus se lirait comme une conversation vide.
    """


class ThreadBusyError(ThreadConflictError):
    """Un run est déjà en cours sur ce fil.

    Deux runs concurrents partiraient du même checkpoint et l'écraseraient l'un
    l'autre — et deux reprises concurrentes d'une même validation
    exécuteraient deux fois le geste approuvé.
    """


class ApprovalPendingError(ThreadConflictError):
    """Un nouveau message alors qu'une validation attend sa décision.

    Laisser passer le message abandonnerait la validation en silence, avec un
    appel d'outil resté sans réponse dans le fil.
    """


class NoPendingApprovalError(ThreadConflictError):
    """Une décision alors qu'aucune validation n'est en attente."""


class ToolsetError(MassAgentsError):
    """Le serveur MCP n'a pas rendu l'outillage attendu.

    Soit il est injoignable, soit il n'expose pas les outils que la répartition
    déclare — un décalage de version entre les deux services. Dans les deux cas
    le run ne peut pas commencer, et le dire au démarrage vaut mieux qu'un agent
    qui découvre au troisième tour qu'il lui manque un outil.
    """


class SuggestionRequestError(MassAgentsError):
    """La demande de suggestions est valide, mais rien ne permet d'y répondre.

    Typiquement : seulement des évènements MASS, et aucun n'est à venir. Le
    message dit quoi changer dans le formulaire.
    """


class SuggestionGenerationError(MassAgentsError):
    """Le modèle n'a pas rendu de suggestions utilisables, même en réessayant.

    Le nombre de posts ne correspond pas, une catégorie n'a pas été demandée,
    ou un post cite une source qui n'existe pas. Rendu en 502 : la demande était
    bonne, c'est la dépendance qui a mal répondu.
    """


class RunLimitError(MassAgentsError):
    """Un plafond a été atteint : tours, jetons, ou durée.

    Ce n'est pas une panne. Le message est fait pour être lu par l'utilisateur
    et dire quoi faire — reformuler plus étroitement, ou ouvrir un nouveau fil.
    """
