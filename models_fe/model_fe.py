# Load Packages
import pandas as pd
import numpy as np
import plotly.express as px

import warnings, sys, os

warnings.filterwarnings("ignore")

from typing import List

from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, mean_absolute_error
from sklearn.preprocessing import MinMaxScaler

import keras

rootpath = ".."
sys.path.insert(0, f"{os.getcwd()}/{rootpath}/models")
import model_prep

step_back = 6  # window size = 6*5 = 30 mins
season_map = {
    "spring": [3, 4, 5],
    "summer": [6, 7, 8],
    "fall": [9, 10, 11],
    "winter": [12, 1, 2],
}


def feature_extr_transfer(
    from_building_name: str,
    from_tower_number: int,
    to_building_name: str,
    to_tower_number: int,
    to_features: List[str],
    to_target: str,
    to_season: str = None,
    from_season: str = None,
    finetuning_percentage: float = 0,
    finetune_epochs: int = 10,
    display_results: bool = True,
    use_delta: bool = True,
):
    # fix inputs
    if from_season == None and to_season != None:
        from_season = to_season

    """
    1. Load data and do LSTM preprocessing
    """

    lstm_to_df, to_first_temp = model_prep.create_preprocessed_lstm_df(
        building_name=to_building_name,
        tower_number=to_tower_number,
        features=to_features,
        target=to_target,
        season=to_season,
        use_delta=use_delta,
    )
    if not to_season:
        to_season = from_season = "allyear"

    print(f"Tower {to_tower_number} first temp: {to_first_temp}")

    """
    2. Convert tower data into a model-compatible shape i.e. get timestepped data as a 3D vector
    """

    X = lstm_to_df.drop(f"{to_target}(t)", axis=1)  # drop target column
    y = lstm_to_df[f"{to_target}(t)"]  # only have target column

    # if no finetuning is required
    if finetuning_percentage == 0:
        # entire set is for testing
        X_test = X
        y_test = y

        # scale feature data
        X_test[X_test.columns] = MinMaxScaler().fit_transform(X_test)

        # create 3d vector form of data
        vec_X_test = model_prep.df_to_3d(
            lstm_dtframe=X_test, num_columns=len(to_features) + 1, step_back=step_back
        )
        vec_y_test = y_test.values
        # print(vec_X_test.shape, vec_y_test.shape)

        # load model
        model = keras.models.load_model(
            f"../models_saved/{from_building_name.lower()}{from_tower_number}_{from_season}_lstm/"
        )

    # if finetuning is required
    else:
        # split train and test set
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=(1 - finetuning_percentage), shuffle=False
        )

        # scale feature data
        scaler = MinMaxScaler()
        scaler = scaler.fit(X_train)
        X_train[X_train.columns] = scaler.transform(X_train)
        X_test[X_test.columns] = scaler.transform(X_test)

        # create 3d vector form of data
        vec_X_train = model_prep.df_to_3d(
            lstm_dtframe=X_train, num_columns=len(to_features) + 1, step_back=step_back
        )
        vec_X_test = model_prep.df_to_3d(
            lstm_dtframe=X_test, num_columns=len(to_features) + 1, step_back=step_back
        )

        vec_y_train = y_train.values
        vec_y_test = y_test.values

        print(
            f"finetuning_percentage: {finetuning_percentage} vec_X_train.shape: {vec_X_train.shape}, vec_X_test.shape: {vec_X_test.shape}, vec_y_train.shape: {vec_y_train.shape}, vec_y_test.shape: {vec_y_test.shape}"
        )

        # load and finetune model
        model = keras.models.load_model(
            f"../models_saved/{from_building_name.lower()}{from_tower_number}_{from_season}_lstm/"
        )

        # freeze lstm layer
        model.layers[0].trainable = False
        # dense layer to be finetuned
        model.layers[1].trainable = False

        model.compile(
            optimizer=keras.optimizers.Adam(1e-5),  # Very low learning rate
            loss="mse",
            metrics=[keras.metrics.BinaryAccuracy()],
        )

        history = model.fit(
            vec_X_train, vec_y_train, epochs=finetune_epochs, verbose=0, shuffle=False
        )

    """
    3. Load model, finetune and predict
    """

    yhat = model.predict(vec_X_test)

    """
    4. Display results
    """

    # show results
    results_df = pd.DataFrame(
        {
            "actual": vec_y_test.reshape((vec_y_test.shape[0])),
            "predicted": yhat.reshape((yhat.shape[0])),
        },
        index=y_test.index,
    )

    if use_delta:
        results_df["actual"] = results_df["actual"] + to_first_temp
        results_df["predicted"] = results_df["predicted"] + to_first_temp

    rmse = np.sqrt(mean_squared_error(results_df["actual"], results_df["predicted"]))
    mabs_error = mean_absolute_error(results_df["actual"], results_df["predicted"])

    # display results
    def display_transfer_results():
        # Create a new DataFrame with the desired 5-minute interval index, and merge the new DataFrame with the original DataFrame
        display_df = pd.DataFrame(
            index=pd.date_range(
                start=results_df.index.min(), end=results_df.index.max(), freq="5min"
            )
        ).merge(results_df, how="left", left_index=True, right_index=True)

        print("RMSE: %.3f" % rmse)

        fig = px.line(display_df, x=display_df.index, y=["actual", "predicted"])
        fig.update_layout(
            title=f"{from_building_name} Tower {from_tower_number} {from_season} model used on {to_building_name} Tower {to_tower_number} {to_season} ({finetuning_percentage*100}% fine-tuning) LSTM Model Results",
            xaxis_title="time",
            yaxis_title=to_target,
        )
        return fig

    if display_results:
        fig = display_transfer_results()

    return rmse, fig, mabs_error
