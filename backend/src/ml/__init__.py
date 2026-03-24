import warnings

# Suppress sklearn warning when LGBMRegressor/Classifier is called with numpy arrays
# instead of DataFrames. All our models build feature arrays from dicts at predict time
# which is intentional — the predictions are correct, just missing column metadata.
warnings.filterwarnings("ignore", message="X does not have valid feature names")
