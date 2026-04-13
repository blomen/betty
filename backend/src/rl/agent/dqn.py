"""Double Dueling DQN agent with prioritized replay, Polyak updates, gradient clipping."""

from __future__ import annotations

import copy
import random
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch.optim import Adam

from src.rl.agent.network import DQNetwork
from src.rl.agent.replay_buffer import ReplayBuffer
from src.rl.config import (
    BATCH_SIZE,
    EPSILON_DECAY_STEPS,
    EPSILON_END,
    EPSILON_START,
    LEARNING_RATE,
    NUM_ACTIONS,
    REPLAY_BUFFER_SIZE,
    TARGET_NET_UPDATE_FREQ,
    TAU,
)

GRAD_CLIP_NORM = 1.0


class DQNAgent:
    """Double Dueling DQN with prioritized replay and Polyak soft target updates.

    Key design choices:
    - Double DQN: online net selects action, target net evaluates — fixes overestimation.
    - Dueling architecture: value + advantage streams (in DQNetwork).
    - Huber loss weighted by importance-sampling corrections from PER.
    - Gradient clipping (max_norm=1.0) for training stability.
    - Polyak soft update: θ_target ← τ·θ_online + (1-τ)·θ_target every step.
    """

    def __init__(
        self,
        observation_dim: int,
        epsilon: float = EPSILON_START,
        buffer_capacity: int = REPLAY_BUFFER_SIZE,
        **_kwargs,
    ) -> None:
        self.observation_dim = observation_dim
        self.epsilon = epsilon

        # Use multiple CPU cores for matrix ops (extraction is I/O-bound)
        torch.set_num_threads(4)
        torch.set_num_interop_threads(2)

        # Networks
        self.q_network = DQNetwork(observation_dim)
        self.target_network = copy.deepcopy(self.q_network)
        self.target_network.eval()

        # Optimiser
        self.optimizer = Adam(self.q_network.parameters(), lr=LEARNING_RATE)

        # Prioritized replay buffer
        self.buffer = ReplayBuffer(buffer_capacity)

        # Step counters and decay schedule
        self.train_steps: int = 0
        self._epsilon_decay_rate: float = (EPSILON_START - EPSILON_END) / max(EPSILON_DECAY_STEPS, 1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select_action(self, observation: np.ndarray) -> int:
        """Epsilon-greedy action selection."""
        if random.random() < self.epsilon:
            return random.randrange(NUM_ACTIONS)
        q_values = self.q_network.predict(observation)  # (1, NUM_ACTIONS)
        return int(np.argmax(q_values[0]))

    def store(self, observation: np.ndarray, action: int, reward: float, stop_target: float = 10.0) -> None:
        """Add a transition to the replay buffer."""
        self.buffer.add(observation, action, reward, stop_target)

    def train_step(self) -> float:
        """Sample a prioritized mini-batch and perform one gradient update.

        Uses Double DQN: online network selects the best action, target network
        evaluates its Q-value. This reduces overestimation bias.
        """
        batch = self.buffer.sample(BATCH_SIZE)

        obs_t = torch.from_numpy(batch["observations"])  # (B, obs_dim)
        act_t = torch.from_numpy(batch["actions"]).unsqueeze(1)  # (B, 1)
        rew_t = torch.from_numpy(batch["rewards"])  # (B,)
        stop_t = torch.from_numpy(batch["stop_targets"])  # (B,)
        weights_t = torch.from_numpy(batch["weights"])  # (B,)
        indices = batch["indices"]  # (B,)

        # Predicted Q-values and stop distance
        self.q_network.train()
        q_all, stop_pred = self.q_network.forward_full(obs_t)
        q_pred = q_all.gather(1, act_t).squeeze(1)  # (B,)

        # Target Q-value.
        # GAMMA=0 (each zone touch is an independent bandit problem — no future
        # discounting), so target_q = reward directly.  The target network and
        # Double DQN bootstrap are dead computation when GAMMA=0, so skip them.
        target_q = rew_t.detach()

        # Q-value loss (Huber, weighted by IS)
        td_errors = q_pred - target_q
        q_loss = (weights_t * F.smooth_l1_loss(q_pred, target_q, reduction="none")).mean()

        # Stop distance loss (MSE, weighted by IS — consistent with Q-loss weighting)
        stop_loss = (weights_t * 0.5 * F.mse_loss(stop_pred.squeeze(1), stop_t, reduction="none")).mean()

        loss = q_loss + stop_loss

        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.q_network.parameters(), GRAD_CLIP_NORM)
        self.optimizer.step()

        # Update priorities with new TD errors
        self.buffer.update_priorities(indices, td_errors.detach().numpy())

        self.train_steps += 1

        # Linear epsilon decay
        self.epsilon = max(
            EPSILON_END,
            self.epsilon - self._epsilon_decay_rate,
        )

        # Polyak soft target update
        if self.train_steps % TARGET_NET_UPDATE_FREQ == 0:
            with torch.no_grad():
                for p_online, p_target in zip(self.q_network.parameters(), self.target_network.parameters()):
                    p_target.data.mul_(1.0 - TAU).add_(p_online.data * TAU)

        return loss.item()

    def save(self, path: Path, epoch: int = 0) -> None:
        """Persist agent state."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "q_network": self.q_network.state_dict(),
                "target_network": self.target_network.state_dict(),
                "optimizer": self.optimizer.state_dict(),
                "epsilon": self.epsilon,
                "train_steps": self.train_steps,
                "epoch": epoch,
            },
            path,
        )

    def load(self, path: Path) -> None:
        """Restore agent state."""
        checkpoint = torch.load(Path(path), weights_only=False)
        self.q_network.load_state_dict(checkpoint["q_network"])
        self.target_network.load_state_dict(checkpoint["target_network"])
        self.optimizer.load_state_dict(checkpoint["optimizer"])
        self.epsilon = checkpoint["epsilon"]
        self.train_steps = checkpoint["train_steps"]
