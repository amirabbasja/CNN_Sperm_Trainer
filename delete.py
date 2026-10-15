import torch
from torch import nn


def make_model():
    layers = []
    for i in range(10):
        layers.append(nn.Linear(256, 256))
        if i < 15:
            layers.append(nn.ReLU())
    return nn.Sequential(*layers)


if __name__ == "__main__":
    torch.manual_seed(1)  # Reproducible random weights

    model = make_model()  # Linear layers initialize randomly
    filename = "./network-trainer/src/weights_5.pth"
    torch.save(model.state_dict(), filename)
    print(f"Saved random weights to {filename}")

    # Example: load the weights into the same architecture
    loaded_model = make_model()
    loaded_model.load_state_dict(
        torch.load(filename, map_location="cpu", weights_only=True)
    )
    loaded_model.eval()