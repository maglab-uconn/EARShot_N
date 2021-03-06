import tensorflow as tf
import numpy as np

pattern_Parameters = tf.contrib.training.HParams(**{    
    'Word_List_File': 'Pronunciation_Data_1K.Temp.txt',
    'Voice_Path': './WAV',
    'Pattern_Path': './PICKLE',
    'Pattern_Metadata_File_Name': 'Metadata.10Talkers.pickle', #'METADATA.PICKLE', #'METADATA.240.PICKLE',
    'Pattern_Use_Bit': 32,  #16 or 32
        
    'Acoutsic_Mode': 'Spectrogram',  #Spectrogram, Mel
    'Semantic_Mode': 'PGD', #SRV, PGD

    'Spectrogram': tf.contrib.training.HParams(**{
        'Sample_Rate': 22050,
        'Dimension': 256,
        'Frame_Length': 10,
        'Frame_Shift': 10,
        }),

    'Mel': tf.contrib.training.HParams(**{
        'Sample_Rate': 22050,    
        'Spectrogram_Dim': 1025,
        'Mel_Dim': 80,
        'Frame_Length': 10,
        'Frame_Shift': 10,
        'Max_Abs': 4,   #If 'None', non symmetric '0 to 1'.
        }),

    'SRV': tf.contrib.training.HParams(**{
        'Size': 300,
        'Assign_Number': 30,    
        }),   
    
    'PGD': tf.contrib.training.HParams(**{
        'Dict_File_Path': 'Word2Vec.Paper1K.Kevin.pydb',
        'Size': 300,
        }), 
    })

model_Parameters = tf.contrib.training.HParams(**{
    'Hidden_Type': 'LSTM', #'ZoneoutLSTM',
    'Hidden_Size': 512,
    'Zoneout_Rate': 0.1,    #Only for ZoneoutLSTM
    'Prenet_Conv': tf.contrib.training.HParams(**{
        'Use': False,
        'Channels': [512, 512, 512],
        'Kernel_Sizes': [5, 5, 5],
        'Strides': [1, 1, 1],
        'Use_Batch_Normalization': True,
        'Dropout_Rate': 0.5, #If 'None', no dropout
        }),
    'Weight_Regularization': tf.contrib.training.HParams(**{
        'Use': False,
        'Except_Keywords': ['lstm', 'gru', 'scrn', 'rnn', 'bias'],
        'Rate': 1e-6
        }),
    'Test_Timing': 200,
    'Checkpoint_Timing': 100,
    'Exclusion_Mode': 'M', #P, T, M, None
    'Test_Only_Identifier_List': None,#[],
    'Max_Epoch_with_Exclusion': 600,
    'Max_Epoch_without_Exclusion': 800,   #This is the value added to 'Max_Epoch_with_Exclusion'.
    'Learning_Rate': 0.002,
    'Batch_Size': 2000,
    'Max_Queue': 100,
    #'Force_Checkpoint_Load': False,
    'Extract_Path': './Test',
    'Result_Split': True
    })