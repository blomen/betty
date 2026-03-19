"""DQN training agent with epsilon-greedy exploration and target network."""
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
    GAMMA,
    LEARNING_RATE,
    NUM_ACTIONS,
    REPLAY_BUFFER_SIZE,
    TARGET_NET_UPDATE_FREQ,
)


class DQNAgent:
    """Deep Q-Network agent with epsilon-greedy exploration and a frozen target network.

    Key design choices:
    - Huber loss (smooth_l1_loss) instead of MSE for stability with outlier rewards.
    - GAMMA=0.0: single-step episodes — target Q equals reward directly, no future
      discounting needed.
    - Linear epsilon decay from EPSILON_START to EPSILON_END over EPSILON_DECAY_STEPS.
    - Target network is a deep copy of q_network, updated every TARGET_NET_UPDATE_FREQ
      train steps by hard copy (no polyak averaging).
    """

    def __init__(
        self,
        observation_dim: int,
        epsilon: float = EPSILON_START,
        buffer_capacity: int = REPLAY_BUFFER_SIZE,
    ) -> None:
        self.observation_dim = observation_dim
        self.epsilon = epsilon

        # Networks
        self.q_network = DQNetwork(observation_dim)
        self.target_network = copy.deepcopy(self.q_network)
        self.target_network.eval()

        # Optimiser
        self.optimizer = Adam(self.q_network.parameters(), lr=LEARNING_RATE)

        # Replay buffer
        self.buffer = ReplayBuffer(buffer_capacity)

        # Step counters and decay schedule
        self.train_steps: int = 0
        self._epsilon_decay_rate: float = (EPSILON_START - EPSILON_END) / max(
            EPSILON_DECAY_STEPS, 1
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select_action(self, observation: np.ndarray) -> int:
        """Epsilon-greedy action selection.

        With probability *epsilon* a uniformly random action is chosen;
        otherwise the action with the highest Q-value is taken.

        Args:
            observation: 1-D float array of shape (observation_dim,).

        Returns:
            Integer action index in [0, NUM_ACTIONS).
        """
        if random.random() < self.epsilon:
            return random.randrange(NUM_ACTIONS)
        q_values = self.q_network.predict(observation)  # (1, NUM_ACTIONS)
        return int(np.argmax(q_values[0]))

    def store(self, observation: np.ndarray, action: int, reward: float) -> None:
        """Convenience wrapper — add a transition to the replay buffer."""
        self.buffer.add(observation, action, reward)

    def train_step(self) -> float:
        """Sample a mini-batch and perform one gradient update.

        Returns:
            Scalar loss value (float).

        Raises:
            ValueError: propagated from ReplayBuffer.sample() if the buffer
                        contains fewer than BATCH_SIZE transitions.
        """
        batch = self.buffer.sample(BATCH_SIZE)

        obs_t = torch.from_numpy(batch["observations"])          # (B, obs_dim)
        act_t = torch.from_numpy(batch["actions"]).unsqueeze(1)  # (B, 1)
        rew_t = torch.from_numpy(batch["rewards"])               # (B,)

        # Predicted Q-values for taken actions
        self.q_network.train()
        q_pred = self.q_network(obs_t).gather(1, act_t).squeeze(1)  # (B,)

        # Target Q-values: GAMMA=0.0 → target = reward directly
        with torch.no_grad():
            target_q = rew_t + GAMMA * self.target_network(obs_t).max(dim=1).values

        loss = F.smooth_l1_loss(q_pred, target_q)

        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

        self.train_steps += 1

        # Linear epsilon decay
        self.epsilon = max(
            EPSILON_END,
            self.epsilon - self._epsilon_decay_rate,
        )

        # Hard-copy target network
        if self.train_steps % TARGET_NET_UPDATE_FREQ == 0:
            self.target_network.load_state_dict(self.q_network.state_dict())

        return loss.item()

    def save(self, path: Path) -> None:
        """Persist agent state to *path*.

        Saves q_network weights, target_network weights, optimizer state,
        current epsilon and train_steps count.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "q_network": self.q_network.state_dict(),
                "target_network": self.target_network.state_dict(),
                "optimizer": self.optimizer.state_dict(),
                "epsilon": self.epsilon,
                "train_steps": self.train_steps,
            },
            path,
        )

    def load(self, path: Path) -> None:
        """Restore agent state from *path*.

        Uses weights_only=False so that the optimizer state (which contains
        Python objects) is deserialised correctly.
        """
        checkpoint = torch.load(Path(path), weights_only=False)
        self.q_network.load_state_dict(checkpoint["q_network"])
        self.target_network.load_state_dict(checkpoint["target_network"])
        self.optimizer.load_state_dict(checkpoint["optimizer"])
        self.epsilon = checkpoint["epsilon"]
        self.train_steps = checkpoint["train_steps"]
